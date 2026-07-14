"""Fault injection into a running TwinModel. Each injector mutates node state or
resizes the capacity pool; time-varying faults (mem) run as sub-processes."""

from __future__ import annotations

from backend.twin.model import TwinModel

_DEFAULT_DELAY_S = 0.06
_DEFAULT_LOSS_P = 0.35


def _take(model: TwinModel, node: str, n: int) -> None:
    """Reserve n capacity slots (held until a remedy returns them)."""
    n = int(n)
    if n <= 0:
        return

    def _proc():
        yield model.slots[node].get(n)
    model.env.process(_proc())
    model.held[node] = model.held.get(node, 0) + n


def _mem_ramp(model: TwinModel, node: str, step: float = 1.0, rate: float = 0.2):
    while True:
        yield model.env.timeout(step)
        model.state[node].service_mult += rate


def inject(model: TwinModel, fault_type: str, component: str, magnitude: float | None = None,
           diff: str | None = None) -> str:
    """Apply `fault_type` to `component` at the model's current sim time."""
    st = model.state[component]
    cap = model.capacity[component]

    if fault_type == "cpu":                      # capacity -70%
        _take(model, component, round(0.7 * cap))
    elif fault_type == "mem":                    # service time grows over time
        model.env.process(_mem_ramp(model, component, rate=(magnitude or 0.2)))
    elif fault_type == "disk":                   # inflate service time on db nodes
        st.service_mult *= 3.0 if component.endswith("-db") else 1.5
    elif fault_type == "delay":                  # +X ms inbound
        st.inbound_delay += (magnitude or _DEFAULT_DELAY_S)
    elif fault_type == "loss":                   # drop p% -> errors
        st.drop_p = min(1.0, magnitude or _DEFAULT_LOSS_P)
    elif fault_type == "socket":                 # cap concurrency
        _take(model, component, max(0, cap - 2))
    elif fault_type == "config_push":            # parse diff effect else treat as delay
        if diff and any(k in diff.lower() for k in ("limit", "replica", "pool", "conn")):
            _take(model, component, round(0.5 * cap))
        else:
            st.inbound_delay += (magnitude or _DEFAULT_DELAY_S)
    else:
        st.inbound_delay += (magnitude or _DEFAULT_DELAY_S)
    return fault_type
