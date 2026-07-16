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
import textwrap
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from backend.agents import transcript as tr
from backend.agents import usage
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
    # The assistant turn verbatim, so history can be replayed back to the API in the
    # real function-calling protocol (assistant.tool_calls -> role:"tool" response).
    # None for Scripted/Replay backends, which never talk to an API and whose history
    # only has to read sensibly.
    raw_message: dict | None = None
    tool_call_id: str | None = None


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
    """Real function-calling client, temperature 0, retried via tenacity.

    Every response's token usage is recorded to `usage.METER`, and the dollar cap
    is checked BEFORE each request. Exceeding it raises SpendCap, which this loop
    treats as any other LLM failure — so rule 11 sends the run to the autopilot
    rather than failing it.
    """

    def __init__(self, model: str, meter: usage.Meter | None = None) -> None:
        from openai import OpenAI
        self._client = OpenAI()
        self.model = model
        self.meter = meter if meter is not None else usage.METER

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8),
           reraise=True)
    def _call(self, messages, tool_specs):
        return self._client.chat.completions.create(
            model=self.model, messages=messages, tools=tool_specs,
            tool_choice="auto", temperature=0,
        )

    def decide(self, messages, tool_specs) -> LLMDecision:
        self.meter.check()                      # before the spend, not after
        resp = self._call(messages, tool_specs)
        u = getattr(resp, "usage", None)
        if u is not None:
            self.meter.record(self.model, getattr(u, "prompt_tokens", 0) or 0,
                              getattr(u, "completion_tokens", 0) or 0)
        msg = resp.choices[0].message
        if getattr(msg, "tool_calls", None):
            tc = msg.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            # Rebuild the assistant turn with ONLY the tool call we are about to
            # execute. The API requires every tool_call in the history to have exactly
            # one matching `role: "tool"` reply; echoing back a parallel batch we only
            # answer the first of would 400 on the next request.
            raw = {"role": "assistant",
                   "tool_calls": [{"id": tc.id, "type": "function",
                                   "function": {"name": tc.function.name,
                                                "arguments": tc.function.arguments or "{}"}}]}
            if msg.content:
                raw["content"] = msg.content
            return LLMDecision(tool=tc.function.name, args=args,
                               raw_message=raw, tool_call_id=tc.id)
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
    """Turn the typed registry into OpenAI function specs.

    The description is the WHOLE docstring, not its first line. It used to be
    `.split("\\n")[0]`, which quietly deleted every caveat anyone wrote — including
    "an empty result means no fact matched THOSE FILTERS, not that the run has no
    evidence", which is the exact misreading that made a live narrator file a report
    of six empty headings. The spec is the only thing the model knows about a tool;
    truncating it to one line is choosing to tell it less.
    """
    specs = []
    for n in names:
        t = REGISTRY[n]
        doc = textwrap.dedent(t.fn.__doc__ or "").strip()
        specs.append({
            "type": "function",
            "function": {
                "name": n,
                "description": doc or n,
                "parameters": t.input_model.model_json_schema(),
            },
        })
    return specs


def record_turn(messages: list[dict], decision: LLMDecision, name: str, summary: str) -> None:
    """Append one (assistant -> tool result) exchange to the history.

    This used to fabricate the exchange as prose:

        {"role": "assistant", "content": f"calling {name}"}
        {"role": "user",      "content": f"{name} -> {summary}"}

    which is not the function-calling protocol, and it taught the model the wrong
    lesson. Shown a history where assistant turns are the literal words "calling
    get_anomalies", gpt-4o eventually produced that sentence as *content* instead of
    a tool call — and `decide()` reads a contentful reply as the final answer. The
    first live run ended `status=completed` with `final_text="calling get_anomalies"`:
    the agent stopped after four steps believing it had written its report, and the
    "report" was an echo of our own placeholder. Attributing the result to the model's
    own tool_call_id is what keeps its next turn grounded in what it actually called.

    Backends with no raw_message (Scripted/Replay) keep the readable prose form —
    they never round-trip to an API, so the protocol does not apply to them.
    """
    if decision.raw_message is not None and decision.tool_call_id is not None:
        messages.append(decision.raw_message)
        messages.append({"role": "tool", "tool_call_id": decision.tool_call_id,
                         "content": summary})
    else:
        messages.append({"role": "assistant", "content": f"calling {name}"})
        messages.append({"role": "user", "content": f"{name} -> {summary}"})


def replayable(cached_status: str | None, cached_steps: list[dict] | None,
               cached_final: str | None) -> bool:
    """Only a COMPLETED transcript WITH STEPS may be replayed.

    A failed run still writes a transcript (rule 13), but that file is a record, not
    a script. Replaying one used to turn an error into a hollow `completed`: the
    ReplayLLM ran out of decisions on call 1, the agent finished having done nothing,
    rule 11's autopilot never fired, and the twin (and with it the remediation) fell
    silently out of the verdict.

    Worse, the hollow run was then written back as `status=completed`, so the
    corruption survived on disk and re-armed itself on the next replay. Requiring
    STEPS — not just a status — is what makes that artifact inert: a transcript with
    nothing in it has nothing to replay, whatever its status line claims.
    """
    return cached_status == STATUS_COMPLETED and bool(cached_steps)


def resolve_llm(agent: str, model: str, llm: LLM | None,
                cached_steps: list[dict] | None, cached_final: str | None,
                cached_status: str | None = None):
    """explicit LLM > OFFLINE replay of a SUCCESSFUL transcript > real OpenAI > None."""
    if llm is not None:
        return llm, False
    if tr.offline():
        if replayable(cached_status, cached_steps, cached_final):
            return ReplayLLM(cached_steps, cached_final), True
        return None, False                      # offline with no usable cache: no API allowed
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
    cached_steps, cached_status, cached_final = tr.read(path)

    resolved, replayed = resolve_llm(agent, model, llm, cached_steps, cached_final,
                                     cached_status)
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
                record_turn(messages, decision, str(name), step.result_summary)
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
                record_turn(messages, decision, name, step.result_summary)
                continue

            summary = tr.summarize(out.model_dump(mode="json"))
            step = tr.TranscriptStep(time.time(), name, decision.args or {}, summary, True)
            steps.append(step)
            if emit:
                emit("agent_step", {"agent": agent, "tool": name,
                                    "args_summary": tr.summarize(decision.args or {}),
                                    "result_summary": summary})
            record_turn(messages, decision, name, summary)
        else:
            status, error = STATUS_ERROR, f"max iterations ({max_iters}) reached"
    except BudgetExceeded as exc:               # e.g. wall clock tripped mid-flight
        status, error = STATUS_BUDGET, str(exc)
    except Exception as exc:                    # LLM raised, network died, anything
        status, error = STATUS_ERROR, str(exc)

    result = AgentResult(agent, status, final_text=final_text, steps=steps,
                         error=error, replayed=replayed)
    # Replay is a READ path. Rewriting the file it just replayed is how the hollow
    # `completed` above got minted and persisted; the recording stays immutable.
    result.transcript_path = str(path if replayed
                                 else tr.write(path, agent, steps, status, final_text))
    if emit:
        emit("agent_done", {"agent": agent, "status": status,
                            "summary": final_text or error or f"{len(steps)} steps"})
    return result
