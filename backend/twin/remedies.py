"""Remediation primitives: a catalog per fault type, an applicator that mutates
the running twin, and `rehearse()` which measures symptom clearance."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.twin.faults import inject
from backend.twin.model import Calibration, TwinModel

_RESTART_DOWNTIME_S = 10.0


@dataclass(frozen=True)
class Remedy:
    name: str
    params: tuple = ()

    def __str__(self) -> str:
        return self.name


REMEDIES: dict[str, list[Remedy]] = {
    "cpu": [Remedy("restart"), Remedy("scale_replicas", (2,)), Remedy("throttle_upstream")],
    "mem": [Remedy("restart"), Remedy("scale_replicas", (2,))],
    "delay": [Remedy("reroute_avoid_edge"), Remedy("rollback_config")],
    "loss": [Remedy("reroute_avoid_edge"), Remedy("rollback_config")],
    "disk": [Remedy("restart"), Remedy("add_capacity")],
    "socket": [Remedy("raise_conn_limit"), Remedy("restart")],
    "config_push": [Remedy("rollback_config")],
}


@dataclass
class RecoveryReport:
    remedy: str
    symptoms_cleared_pct: float
    sim_time_to_recover_s: float
    residual_symptoms: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)


def _remedy_of(name_or_remedy) -> Remedy:
    return name_or_remedy if isinstance(name_or_remedy, Remedy) else Remedy(str(name_or_remedy))


def apply(model: TwinModel, remedy, component: str) -> None:
    """Apply a remedy to the running twin at the current sim time."""
    r = _remedy_of(remedy)
    st = model.state[component]
    cap = model.capacity[component]
    name = r.name

    if name == "restart":
        st.down = True

        def _restart():
            yield model.env.timeout(_RESTART_DOWNTIME_S)
            st.down = False
            st.service_mult, st.inbound_delay, st.drop_p = 1.0, 0.0, 0.0
            model.slots[component].put(model.held.get(component, 0))  # restore capacity
            model.held[component] = 0
        model.env.process(_restart())

    elif name == "scale_replicas":
        factor = r.params[0] if r.params else 2
        model.slots[component].put(int(cap * (factor - 1)))

    elif name in ("add_capacity", "raise_conn_limit"):
        model.slots[component].put(model.held.get(component, 0) or cap)
        model.held[component] = 0

    elif name == "rollback_config":                 # inverse of the injected config effect
        st.inbound_delay = 0.0
        st.service_mult = 1.0
        model.slots[component].put(model.held.get(component, 0))
        model.held[component] = 0

    elif name == "reroute_avoid_edge":              # route around the faulted path
        st.inbound_delay = 0.0
        st.drop_p = 0.0

    elif name == "throttle_upstream":               # reduce inbound rate 30%
        model.rate_mult *= 0.7


# ---------------------------------------------------------------------------
def _symptom(baseline: list[float], observed: list[float]) -> bool:
    # latency_mean up >50% or error_rate up >0.05
    return observed[0] > baseline[0] * 1.5 + 1e-4 or observed[2] > baseline[2] + 0.05


def _cleared(baseline: list[float], recovered: list[float]) -> bool:
    return recovered[0] <= baseline[0] * 1.3 + 1e-4 and recovered[2] <= baseline[2] + 0.05


def rehearse(
    topology,
    component: str,
    fault_type: str,
    remedy,
    *,
    calibration: Calibration | None = None,
    seed: int = 0,
) -> RecoveryReport:
    """Run a faulted twin, apply the remedy mid-sim, and measure symptom
    clearance vs the faulted baseline."""
    r = _remedy_of(remedy)
    warmup, t_fault, t_remedy = 8.0, 8.0, 20.0
    end = t_remedy + _RESTART_DOWNTIME_S + 14.0

    model = TwinModel(topology, calibration=calibration, seed=seed)
    hooks = [
        (t_fault, lambda m: inject(m, fault_type, component)),
        (t_remedy, lambda m: apply(m, r, component)),
    ]
    model.run(end, hooks=hooks)

    comps = model.components()
    base = {c: model.aggregate(c, 0.0, t_fault) for c in comps}
    faulted = {c: model.aggregate(c, t_fault, t_remedy) for c in comps}
    during = {c: model.aggregate(c, t_remedy, t_remedy + _RESTART_DOWNTIME_S + 2.0) for c in comps}
    recovered = {c: model.aggregate(c, end - 10.0, end) for c in comps}

    symptoms = [c for c in comps if _symptom(base[c], faulted[c])]
    cleared = [c for c in symptoms if _cleared(base[c], recovered[c])]
    residual = [c for c in symptoms if c not in cleared]
    # side effects: components made worse by the remedy that weren't symptoms
    side = [c for c in comps if c not in symptoms
            and (during[c][2] > base[c][2] + 0.1 or during[c][0] > base[c][0] * 2.0 + 1e-4)]

    pct = 100.0 * len(cleared) / len(symptoms) if symptoms else 0.0
    recover_s = (_RESTART_DOWNTIME_S + 4.0) if r.name == "restart" else 4.0
    return RecoveryReport(
        remedy=r.name,
        symptoms_cleared_pct=round(pct, 1),
        sim_time_to_recover_s=round(recover_s, 1),
        residual_symptoms=residual,
        side_effects=side,
    )
