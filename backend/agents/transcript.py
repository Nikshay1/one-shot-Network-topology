"""Agent transcripts: JSONL writer/reader + deterministic cache key + replay.

Rule 13: every agent run writes a transcript that is cached for OFFLINE demo
replay. The cache key is deterministic:

    key = sha256(run_id + ledger digest AT AGENT START + prompt version)

so the same investigation over the same evidence with the same prompt resolves to
the same cached transcript. With OFFLINE=1 the cached decisions are replayed
through the harness — the tools re-execute locally and the identical `agent_step`
SSE events are emitted, with ZERO API calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

DEFAULT_DIR = Path("data/transcripts")
SUMMARY_MAX = 200


@dataclass
class TranscriptStep:
    ts: float
    tool: str
    args: dict = field(default_factory=dict)
    result_summary: str = ""
    ok: bool = True


def summarize(obj) -> str:
    """Result summary, <= 200 chars (the transcript is a log, not a data store)."""
    try:
        s = json.dumps(obj, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= SUMMARY_MAX else s[: SUMMARY_MAX - 3] + "..."


def ledger_digest(ledger) -> str:
    """A stable digest of the ledger's contents at this instant."""
    try:
        ids = sorted(f.fact_id for f in ledger.query(limit=100_000))
    except Exception:                                   # pragma: no cover - defensive
        ids = []
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]


def cache_key(run_id: str, digest: str, prompt_version: str) -> str:
    raw = f"{run_id}|{digest}|{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def path_for(directory: str | Path, agent: str, key: str) -> Path:
    return Path(directory) / f"{agent}-{key}.jsonl"


def write(path: str | Path, agent: str, steps: list[TranscriptStep],
          status: str, final_text: str | None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for s in steps:
            fh.write(json.dumps({"type": "step", "agent": agent, **asdict(s)}) + "\n")
        fh.write(json.dumps({"type": "result", "agent": agent, "status": status,
                             "final_text": final_text, "ts": time.time()}) + "\n")
    return p


def read(path: str | Path):
    """Return (steps, status, final_text) or (None, None, None) if absent."""
    p = Path(path)
    if not p.exists():
        return None, None, None
    steps: list[dict] = []
    status = final_text = None
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") == "result":
            status, final_text = rec.get("status"), rec.get("final_text")
        elif rec.get("type") == "step":
            steps.append(rec)
    return steps, status, final_text


def replay_events(steps: list[dict], agent: str,
                  emit: Callable[[str, dict], None] | None) -> None:
    """Re-emit the cached steps as SSE so the demo shows identical 'thinking'."""
    if emit is None:
        return
    for s in steps:
        emit("agent_step", {"agent": agent, "tool": s.get("tool"),
                            "args_summary": summarize(s.get("args")),
                            "result_summary": s.get("result_summary", "")})


def offline() -> bool:
    return os.getenv("OFFLINE", "0") == "1"
