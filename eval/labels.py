"""Ground-truth access — the ONLY reader of data/labels outside /scenarios.

Rule 4 lives or dies here: `fault_service`, `inject_time` and
`ground_truth_innocent` are read in this module and never handed to pipeline code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LABELS_DIR = Path("data/labels")


@dataclass
class Label:
    case_id: str
    fault_service: str | None
    fault_type: str
    inject_time: float
    synthetic: bool
    scenario_type: str | None = None
    ground_truth_innocent: list[str] = field(default_factory=list)
    expected_root_cause: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def truth(self) -> str | None:
        """The component the ranker is supposed to name — None when the scenario has
        no single right answer. The `ambiguous` suite is deliberately unscoreable:
        two components are equally consistent with the evidence, so scoring it as a
        hit-or-miss would be scoring a coin flip."""
        return self.expected_root_cause or self.fault_service


def load(case_id: str, labels_dir: str | Path = LABELS_DIR) -> Label | None:
    p = Path(labels_dir) / f"{case_id}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return Label(
        case_id=d["case_id"], fault_service=d.get("fault_service"), fault_type=d.get("fault_type", ""),
        inject_time=float(d.get("inject_time", 0.0)), synthetic=bool(d.get("synthetic", False)),
        scenario_type=d.get("scenario_type"),
        ground_truth_innocent=list(d.get("ground_truth_innocent") or []),
        expected_root_cause=d.get("expected_root_cause"), raw=d,
    )


def load_all(labels_dir: str | Path = LABELS_DIR) -> list[Label]:
    out = [load(p.stem, labels_dir) for p in sorted(Path(labels_dir).glob("*.json"))]
    return [x for x in out if x is not None]


def re2ss_cases(labels_dir: str | Path = LABELS_DIR) -> list[Label]:
    """The REAL RE2-SS cases — a label with no `synthetic: true`."""
    return [x for x in load_all(labels_dir) if not x.synthetic]


def synthetic_cases(labels_dir: str | Path = LABELS_DIR) -> list[Label]:
    return [x for x in load_all(labels_dir) if x.synthetic]
