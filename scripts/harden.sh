#!/usr/bin/env bash
# The demo cannot fail.
#
#   1. every demo scenario has a WARM cache (a cold cache on stage is the failure)
#   2. every demo scenario cold-starts 3x, deterministically, with timings recorded
#   3. KILL-NETWORK: no OPENAI_API_KEY + OFFLINE=1, the whole path still passes —
#      including the agent transcript replay and the remediation panel
#
#   bash scripts/harden.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8

PY=""
for cand in python python3 py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; assert sys.version_info>=(3,11)" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "no working python >=3.11 interpreter found" >&2; exit 1; }
echo "using interpreter: $PY"

TIMINGS="data/harden_timings.json"
mkdir -p data

# =====================================================================
echo "== HARDEN 1/3: every demo scenario has a warm cache =="
# =====================================================================
"$PY" - <<'PYEOF'
from backend.narrate import cache
demo = cache.demo_scenarios()
cold = [v["variant_id"] for v in demo if cache.get("demo", cache.key_for(v["variant_id"])) is None]
if cold:
    raise SystemExit(f"COLD CACHE for {cold}\n  fix: make warm-cache")
print(f"  {len(demo)} demo scenarios, all warm")
PYEOF

# =====================================================================
echo "== HARDEN 2/3: cold-start every demo scenario 3x (deterministic + timed) =="
# =====================================================================
"$PY" - <<'PYEOF'
import json, shutil, time
from pathlib import Path
from backend.narrate import cache
from backend.pipeline import run as pipeline_run

REPEATS = 3
rows = []
for v in cache.demo_scenarios():
    cid = v["variant_id"]
    tops, times = [], []
    for i in range(REPEATS):
        # cold start: this run's ledger is thrown away first, so nothing carries over
        led = Path("data/ledger_harden")
        shutil.rmtree(led, ignore_errors=True)
        t0 = time.monotonic()
        r = pipeline_run(cid, ledger_dir=led, run_id=f"harden-{cid}")
        times.append(round(time.monotonic() - t0, 3))
        tops.append(r.hypotheses[0].suspect_component if r.hypotheses else None)
        assert r.hypotheses, f"{cid}: cold start {i+1} produced NO verdict (rule 11)"
        assert r.narration is not None, f"{cid}: cold start {i+1} produced no narration"
    assert len(set(tops)) == 1, f"{cid}: cold starts disagree on top-1: {tops}"
    rows.append({"case_id": cid, "scenario_type": v["scenario_type"], "top1": tops[0],
                 "times_s": times, "median_s": sorted(times)[len(times)//2]})
    print(f"  {cid:<24} top1={tops[0]:<14} x{REPEATS} stable  times={times}s")

Path("data/harden_timings.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(f"  timings -> data/harden_timings.json "
      f"(slowest median {max(r['median_s'] for r in rows)}s)")
PYEOF

# =====================================================================
echo "== HARDEN 3/3: KILL-NETWORK (no key, OFFLINE=1) full demo path =="
# =====================================================================
env -u OPENAI_API_KEY OFFLINE=1 "$PY" - <<'PYEOF'
import json, os
from pathlib import Path
from fastapi.testclient import TestClient

assert not os.getenv("OPENAI_API_KEY"), "the key is still set — this proves nothing"
assert os.getenv("OFFLINE") == "1"

from backend.api.app import create_app
from backend.narrate import cache

case = cache.demo_scenarios()[0]["variant_id"]
with TestClient(create_app()) as client:
    body = client.post(f"/case/{case}/run",
                       json={"speed": 0, "seed": 42, "twin_enabled": True}).json()
    rid = body["run_id"]

    seen, name = [], None
    with client.stream("GET", body["stream"]) as resp:
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                seen.append(name)
                if name in ("pipeline_done", "pipeline_error"):
                    break
    assert seen[-1] == "pipeline_done", f"kill-network run ended on {seen[-1]}"

    # a verdict exists, with no network at all (rule 11)
    v = client.get(f"/run/{rid}/verdict").json()
    assert v["done"] and v["hypotheses"], "no verdict offline"

    # the AGENT TRANSCRIPT is served and is well-formed (rule 13)
    t = client.get(f"/run/{rid}/agent/investigator/transcript")
    assert t.status_code == 200, f"no offline transcript: {t.text[:200]}"
    records = [json.loads(x) for x in t.text.splitlines() if x.strip()]
    assert records and records[-1]["type"] == "result", "transcript has no terminal record"
    replayed_steps = [r for r in records if r["type"] == "step"]
    if not replayed_steps:
        # Say this out loud rather than let a green tick imply replay was proven.
        print("  NOTE: the investigator transcript holds 0 steps — no agent has ever "
              "recorded a run here (no OPENAI_API_KEY has been used yet), so this "
              "asserts the transcript SURFACE offline, not step replay. Rule 11 "
              "carried the run instead. Record a live run with a key to test replay.")

    # the REMEDIATION PANEL still renders
    r = client.get(f"/run/{rid}/remediation")
    assert r.status_code == 200, f"no offline remediation: {r.text[:200]}"
    rep = r.json()
    assert rep["status"] in ("ok", "uncertain", "skipped", "error")

    # and the report PDF
    pdf = client.get(f"/run/{rid}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # This is the regression guard for the poisoned-transcript bug: a hollow replay
    # made the investigator "complete" doing nothing, so the autopilot never ran, the
    # twin never happened, and the panel silently degraded to `skipped`.
    assert rep["status"] in ("ok", "uncertain"), (
        f"remediation degraded to {rep['status']!r} offline: {rep['caveat']}")
    n_steps = seen.count("agent_step")
    print(f"  {case}: pipeline_done, verdict top1={v['hypotheses'][0]['suspect_component']}, "
          f"transcript={len(records)} records ({len(replayed_steps)} replayed steps), "
          f"agent_step x{n_steps}, remediation={rep['status']}, PDF ok — ZERO API calls")
PYEOF

echo "HARDEN OK"
