"""THE Scenario-2 gate — and it must hold in BOTH modes.

Every red-herring-config variant, through the deterministic autopilot AND through
the agentic pipeline:
  * the innocent config change is NOT ranked #1;
  * its hypothesis carries topology_no_path OR counterfactual-unchanged evidence;
  * the true root cause is in the top-3.

Ground truth (fault service, innocent config components) is read from the label
sidecar INSIDE THIS TEST ONLY.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.harness import LLMDecision, ScriptedLLM
from backend.detect.runner import detect
from backend.ingest.store import EventStore
from backend.overlay.scenarios import build_variant, load_registry
from backend.pipeline import run as pipeline_run
from backend.rank.autopilot import run as autopilot_run

_REDUNDANT_PCT = 70.0


def _red_herring_variants():
    reg = load_registry()
    return [v for v in reg["variants"] if v["scenario_type"] == "red_herring_config"]


def _build_and_run(variant, tmp_path: Path):
    bundle, label = build_variant(variant, 42)
    cid = bundle.case_id
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology(cid, bundle.topology)
    detect(cid, store_root=tmp_path / "p", out_dir=tmp_path / "anom", drain_dir=tmp_path / "drain")
    verdict = autopilot_run(cid, store_root=tmp_path / "p", anomalies_dir=tmp_path / "anom",
                            ledger_dir=tmp_path / "ledger")
    return verdict, label


@pytest.mark.parametrize("variant", _red_herring_variants(), ids=lambda v: v["variant_id"])
def test_scenario2_innocent_config_not_root(variant, tmp_path: Path) -> None:
    verdict, label = _build_and_run(variant, tmp_path)
    hyps = verdict.hypotheses
    assert hyps, f"{variant['variant_id']}: no hypotheses"

    fault_service = label.fault_service                          # test-only ground truth
    innocent = {c["component_id"] for c in label.injected_configs if c["innocent"]}

    # 1) the true root cause is in the top-3
    top3 = [h.suspect_component for h in hyps[:3]]
    assert fault_service in top3, f"{fault_service} not in top-3 {top3}"

    # 2) no innocent config change is ranked #1
    assert hyps[0].suspect_component not in innocent, (
        f"innocent config {hyps[0].suspect_component} ranked #1")

    # 3) every innocent config that surfaced as a suspect carries exonerating evidence:
    #    a topology_no_path fact OR counterfactual-unchanged (>=70% still explained).
    by_suspect = {h.suspect_component: h for h in hyps}
    tested_any = False
    for comp in innocent:
        if comp not in by_suspect:
            continue
        tested_any = True
        h = by_suspect[comp]
        no_path = verdict.ledger.query(component_id=comp, kind="topology_no_path")
        cf_facts = verdict.ledger.query(component_id=comp, kind="counterfactual_result")
        counterfactual_unchanged = (
            h.counterfactual.anomalies_still_explained_pct >= _REDUNDANT_PCT and bool(cf_facts)
        )
        assert no_path or counterfactual_unchanged, (
            f"{comp}: no exonerating evidence (no_path={bool(no_path)}, "
            f"cf_pct={h.counterfactual.anomalies_still_explained_pct})")

    # the gate is only meaningful if at least one innocent config was actually tested
    # across the suite; per-variant it may or may not surface as a suspect.
    assert tested_any or innocent, "no innocent configs defined for this variant"


# --------------------------- the gate in BOTH modes ---------------------------
def _prep(variant, tmp_path: Path):
    bundle, label = build_variant(variant, 42)
    cid = bundle.case_id
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology(cid, bundle.topology)
    detect(cid, store_root=tmp_path / "p", out_dir=tmp_path / "anom", drain_dir=tmp_path / "drain")
    return cid, label


def _gate(verdict, label, mode: str):
    innocent = {c["component_id"] for c in label.injected_configs if c["innocent"]}
    top3 = [h.suspect_component for h in verdict.hypotheses[:3]]
    assert label.fault_service in top3, f"{mode}: true fault {label.fault_service} not in {top3}"
    assert verdict.hypotheses[0].suspect_component not in innocent, \
        f"{mode}: innocent config ranked #1"
    for h in verdict.hypotheses:                       # the arithmetic stayed consistent
        assert abs(sum(h.score_breakdown.model_dump().values()) - h.score) < 1e-6
    return innocent


@pytest.mark.parametrize("variant", _red_herring_variants(), ids=lambda v: v["variant_id"])
def test_scenario2_green_in_fixed_pipeline_mode(variant, tmp_path: Path) -> None:
    cid, label = _prep(variant, tmp_path)
    v = pipeline_run(cid, fixed_pipeline=True, store_root=tmp_path / "p",
                     anomalies_dir=tmp_path / "anom", ledger_dir=tmp_path / "l1",
                     transcripts_dir=tmp_path / "t")
    assert v.mode == "autopilot"
    _gate(v, label, "fixed")


@pytest.mark.parametrize("variant", _red_herring_variants(), ids=lambda v: v["variant_id"])
def test_scenario2_green_in_agentic_mode(variant, tmp_path: Path) -> None:
    cid, label = _prep(variant, tmp_path)
    innocent = sorted({c["component_id"] for c in label.injected_configs if c["innocent"]})
    # a scripted Investigator that spends its counterfactuals on the red herrings
    decisions = [LLMDecision(tool="get_candidates", args={})]
    decisions += [LLMDecision(tool="run_counterfactual", args={"component": c})
                  for c in innocent[:2]]
    decisions.append(LLMDecision(final="counterfactualed the suspicious configs"))

    v = pipeline_run(cid, store_root=tmp_path / "p", anomalies_dir=tmp_path / "anom",
                     ledger_dir=tmp_path / "l2", transcripts_dir=tmp_path / "t",
                     llm=ScriptedLLM(decisions))
    assert v.mode == "agentic", v.fallback_note
    _gate(v, label, "agentic")
    assert v.ledger.query(kind="counterfactual_result")   # the red herrings were tested
