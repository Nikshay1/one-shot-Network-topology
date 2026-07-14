"""Append-only evidence ledger — one JSONL file per run.

Each fact is a schema-valid `LedgerRecord`. `query()` is the ONLY read surface
exposed to agents and the narrator; they never read events/anomalies directly.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from backend.models import LedgerKind, LedgerRecord, Window

_DEFAULT_DIR = Path("data/ledger")


class Ledger:
    def __init__(self, run_id: str, case_id: str, ledger_dir: str | Path = _DEFAULT_DIR) -> None:
        self.run_id = run_id
        self.case_id = case_id
        self.path = Path(ledger_dir) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._counters: dict[str, int] = defaultdict(int)
        # seed counters from any existing file so ids stay unique across reopen
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._counters[self._id_key(_first_component(line))] += 1

    # -- ids --------------------------------------------------------------
    @staticmethod
    def _id_key(component: str) -> str:
        return component

    def _next_fact_id(self, component_ids: list[str]) -> str:
        comp = component_ids[0] if component_ids else "system"
        seq = self._counters[comp]
        self._counters[comp] = seq + 1
        return f"fact-{comp.replace('-', '_')}-{seq:04d}"

    # -- core append ------------------------------------------------------
    def write(
        self,
        kind: LedgerKind,
        statement: str,
        component_ids: list[str],
        event_ids: list[str],
        modality: str,
        ts_range: tuple[float, float],
        confidence: float,
        hypothesis_id: str | None = None,
    ) -> str:
        record = LedgerRecord(
            fact_id=self._next_fact_id(component_ids),
            case_id=self.case_id,
            kind=kind,
            statement=statement[:300],
            component_ids=component_ids,
            event_ids=event_ids,
            modality=modality,  # type: ignore[arg-type]
            ts_range=Window(start=float(ts_range[0]), end=float(ts_range[1])),
            confidence=float(confidence),
            hypothesis_id=hypothesis_id,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")
        return record.fact_id

    # -- named writers (one per LedgerKind) -------------------------------
    def anomaly_observed(self, statement, component_ids, event_ids, ts_range,
                         modality="mixed", confidence=0.9, hypothesis_id=None) -> str:
        return self.write("anomaly_observed", statement, component_ids, event_ids,
                          modality, ts_range, confidence, hypothesis_id)

    def anomaly_absent(self, statement, component_ids, ts_range,
                       modality="metric", confidence=0.8, hypothesis_id=None) -> str:
        return self.write("anomaly_absent", statement, component_ids, [],
                          modality, ts_range, confidence, hypothesis_id)

    def topology_path(self, statement, component_ids, event_ids=(), ts_range=(0.0, 0.0),
                      confidence=1.0, hypothesis_id=None) -> str:
        return self.write("topology_path", statement, component_ids, list(event_ids),
                          "topology", ts_range, confidence, hypothesis_id)

    def topology_no_path(self, statement, component_ids, ts_range=(0.0, 0.0),
                         confidence=1.0, hypothesis_id=None) -> str:
        return self.write("topology_no_path", statement, component_ids, [],
                          "topology", ts_range, confidence, hypothesis_id)

    def temporal_order(self, statement, component_ids, event_ids, ts_range,
                       confidence=0.9, hypothesis_id=None) -> str:
        return self.write("temporal_order", statement, component_ids, event_ids,
                          "mixed", ts_range, confidence, hypothesis_id)

    def config_change_observed(self, statement, component_ids, event_ids, ts_range,
                               confidence=1.0, hypothesis_id=None) -> str:
        return self.write("config_change_observed", statement, component_ids, event_ids,
                          "config", ts_range, confidence, hypothesis_id)

    def hypothesis_scored(self, statement, component_ids, ts_range, hypothesis_id,
                          confidence=0.7) -> str:
        return self.write("hypothesis_scored", statement, component_ids, [],
                          "derived", ts_range, confidence, hypothesis_id)

    def investigation_note(self, statement, component_ids, event_ids=(), ts_range=(0.0, 0.0),
                           modality="derived", confidence=0.7, hypothesis_id=None) -> str:
        return self.write("investigation_note", statement, component_ids, list(event_ids),
                          modality, ts_range, confidence, hypothesis_id)

    def counterfactual_result(self, statement, component_ids, ts_range, hypothesis_id,
                              confidence=0.9) -> str:
        return self.write("counterfactual_result", statement, component_ids, [],
                          "derived", ts_range, confidence, hypothesis_id)

    def twin_result(self, statement, component_ids, ts_range, hypothesis_id,
                    confidence=0.86) -> str:
        return self.write("twin_result", statement, component_ids, [],
                          "derived", ts_range, confidence, hypothesis_id)

    def remediation_result(self, statement, component_ids, ts_range, hypothesis_id,
                           confidence=0.9) -> str:
        return self.write("remediation_result", statement, component_ids, [],
                          "derived", ts_range, confidence, hypothesis_id)

    def coverage_gap(self, statement, component_ids, ts_range=(0.0, 0.0),
                     modality="metric", confidence=0.5, hypothesis_id=None) -> str:
        return self.write("coverage_gap", statement, component_ids, [],
                          modality, ts_range, confidence, hypothesis_id)

    # -- the only read surface -------------------------------------------
    def query(
        self,
        component_id: str | None = None,
        kind: str | None = None,
        hypothesis_id: str | None = None,
        limit: int = 50,
    ) -> list[LedgerRecord]:
        if not self.path.exists():
            return []
        out: list[LedgerRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = LedgerRecord.model_validate_json(line)
            if component_id is not None and component_id not in rec.component_ids:
                continue
            if kind is not None and rec.kind != kind:
                continue
            if hypothesis_id is not None and rec.hypothesis_id != hypothesis_id:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out


def _first_component(json_line: str) -> str:
    import json
    try:
        comps = json.loads(json_line).get("component_ids") or []
        return comps[0] if comps else "system"
    except (ValueError, KeyError):  # pragma: no cover
        return "system"
