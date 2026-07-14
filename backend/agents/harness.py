"""Generic bounded ReAct harness over OpenAI function-calling (no agent frameworks).

Per agent it exposes ONLY that agent's tool subset. Every call goes through the
Budget; every step is appended to the transcript AND emitted as an `agent_step`
SSE event. The loop terminates on the model's final message, BudgetExceeded
(calls / cost points / wall clock), or an error — and ALL termination paths
return a well-formed AgentResult.

No agent may call another agent: the tool registry contains no agent-invoking
tool, and `tools` is an explicit allow-list checked on every decision.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from backend.agents import transcript as tr
from backend.agents.budget import Budget, BudgetExceeded
from backend.agents.tools import REGISTRY, ToolContext, call_tool

MAX_ITERS = 24

STATUS_COMPLETED = "completed"
STATUS_BUDGET = "budget_exhausted"
STATUS_ERROR = "error"


# =========================================================================
# LLM backends
# =========================================================================
@dataclass
class LLMDecision:
    tool: str | None = None
    args: dict | None = None
    final: str | None = None


class LLM(Protocol):
    def decide(self, messages: list[dict], tool_specs: list[dict]) -> LLMDecision: ...


class ScriptedLLM:
    """Deterministic decisions for tests — zero API calls."""

    def __init__(self, decisions: list[LLMDecision]) -> None:
        self._q = list(decisions)
        self.calls = 0

    def decide(self, messages, tool_specs) -> LLMDecision:
        self.calls += 1
        if not self._q:
            return LLMDecision(final="done")
        return self._q.pop(0)


class ReplayLLM:
    """Replays cached transcript decisions; tools re-execute locally, no API calls."""

    def __init__(self, steps: list[dict], final_text: str | None = None) -> None:
        self._q = [s for s in steps if s.get("tool")]
        self._final = final_text or "replayed"
        self.calls = 0

    def decide(self, messages, tool_specs) -> LLMDecision:
        self.calls += 1
        if not self._q:
            return LLMDecision(final=self._final)
        s = self._q.pop(0)
        return LLMDecision(tool=s["tool"], args=s.get("args") or {})


class OpenAIClient:
    """Real function-calling client, temperature 0, retried via tenacity."""

    def __init__(self, model: str) -> None:
        from openai import OpenAI
        self._client = OpenAI()
        self.model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8),
           reraise=True)
    def _call(self, messages, tool_specs):
        return self._client.chat.completions.create(
            model=self.model, messages=messages, tools=tool_specs,
            tool_choice="auto", temperature=0,
        )

    def decide(self, messages, tool_specs) -> LLMDecision:
        resp = self._call(messages, tool_specs)
        msg = resp.choices[0].message
        if getattr(msg, "tool_calls", None):
            tc = msg.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            return LLMDecision(tool=tc.function.name, args=args)
        return LLMDecision(final=msg.content or "")


# =========================================================================
# Result
# =========================================================================
@dataclass
class AgentResult:
    agent: str
    status: str                       # completed | budget_exhausted | error
    final_text: str | None = None
    transcript_path: str | None = None
    steps: list[tr.TranscriptStep] = field(default_factory=list)
    error: str | None = None
    replayed: bool = False

    @property
    def tools_used(self) -> list[str]:
        return [s.tool for s in self.steps if s.ok]


def tool_specs(names: list[str]) -> list[dict]:
    specs = []
    for n in names:
        t = REGISTRY[n]
        specs.append({
            "type": "function",
            "function": {
                "name": n,
                "description": (t.fn.__doc__ or n).strip().split("\n")[0],
                "parameters": t.input_model.model_json_schema(),
            },
        })
    return specs


def resolve_llm(agent: str, model: str, llm: LLM | None,
                cached_steps: list[dict] | None, cached_final: str | None):
    """explicit LLM > OFFLINE replay > real OpenAI > None."""
    if llm is not None:
        return llm, False
    if tr.offline():
        if cached_steps is not None:
            return ReplayLLM(cached_steps, cached_final), True
        return None, False                      # offline with no cache: no API allowed
    if not os.getenv("OPENAI_API_KEY"):
        return None, False
    try:
        return OpenAIClient(model), False
    except Exception:                           # pragma: no cover - env/import issues
        return None, False


# =========================================================================
# The loop
# =========================================================================
def run_agent(
    *,
    agent: str,
    model: str,
    system_prompt: str,
    task: str,
    tools: list[str],
    ctx: ToolContext,
    budget: Budget,
    run_id: str,
    prompt_version: str,
    llm: LLM | None = None,
    transcripts_dir: str | Path = tr.DEFAULT_DIR,
    emit: Callable[[str, dict], None] | None = None,
    max_iters: int = MAX_ITERS,
) -> AgentResult:
    # cache key is bound to the ledger state AT AGENT START
    key = tr.cache_key(run_id, tr.ledger_digest(ctx.ledger), prompt_version)
    path = tr.path_for(transcripts_dir, agent, key)
    cached_steps, _cached_status, cached_final = tr.read(path)

    resolved, replayed = resolve_llm(agent, model, llm, cached_steps, cached_final)
    steps: list[tr.TranscriptStep] = []

    if resolved is None:                        # nothing to drive the loop
        result = AgentResult(agent, STATUS_ERROR, error="no LLM available (no key / no cache)")
        result.transcript_path = str(tr.write(path, agent, steps, result.status, None))
        if emit:
            emit("agent_done", {"agent": agent, "status": result.status, "summary": result.error})
        return result

    specs = tool_specs(tools)
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": task}]
    status, final_text, error = STATUS_COMPLETED, None, None
    budget.start()

    try:
        for _ in range(max_iters):
            decision = resolved.decide(messages, specs)

            if decision.final is not None:
                final_text = decision.final
                status = STATUS_COMPLETED
                break

            name = decision.tool
            if name not in tools:               # off the allow-list (incl. any agent tool)
                step = tr.TranscriptStep(time.time(), str(name), decision.args or {},
                                         f"error: tool {name!r} not available to {agent}", False)
                steps.append(step)
                messages.append({"role": "user", "content": step.result_summary})
                continue

            try:
                out = call_tool(name, decision.args or {}, ctx, budget=budget)
            except BudgetExceeded as exc:
                status, error = STATUS_BUDGET, str(exc)
                break
            except Exception as exc:            # a tool failed: log it, let the agent adapt
                step = tr.TranscriptStep(time.time(), name, decision.args or {},
                                         f"error: {exc}", False)
                steps.append(step)
                messages.append({"role": "user", "content": step.result_summary})
                continue

            summary = tr.summarize(out.model_dump(mode="json"))
            step = tr.TranscriptStep(time.time(), name, decision.args or {}, summary, True)
            steps.append(step)
            if emit:
                emit("agent_step", {"agent": agent, "tool": name,
                                    "args_summary": tr.summarize(decision.args or {}),
                                    "result_summary": summary})
            messages.append({"role": "assistant", "content": f"calling {name}"})
            messages.append({"role": "user", "content": f"{name} -> {summary}"})
        else:
            status, error = STATUS_ERROR, f"max iterations ({max_iters}) reached"
    except BudgetExceeded as exc:               # e.g. wall clock tripped mid-flight
        status, error = STATUS_BUDGET, str(exc)
    except Exception as exc:                    # LLM raised, network died, anything
        status, error = STATUS_ERROR, str(exc)

    result = AgentResult(agent, status, final_text=final_text, steps=steps,
                         error=error, replayed=replayed)
    result.transcript_path = str(tr.write(path, agent, steps, status, final_text))
    if emit:
        emit("agent_done", {"agent": agent, "status": status,
                            "summary": final_text or error or f"{len(steps)} steps"})
    return result
