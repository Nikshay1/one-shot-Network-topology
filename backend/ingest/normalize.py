"""Single source of truth for component-id and event-id construction.

Rule 2 of VERDICT: `component_id` may ONLY be produced by
:func:`normalize_component`. Never do ad-hoc string cleanup elsewhere.
Every event id must be produced by :func:`make_event_id`.

Runnable as a CLI:

    py -m backend.ingest.normalize normalize "Front-End-6c4d8b9f8d-abcde"
    py -m backend.ingest.normalize event-id metric front-end 1
"""

from __future__ import annotations

import re
import sys
from typing import Final

# --- Contract regexes (kept in lock-step with /contracts and backend/models.py) ---
COMPONENT_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]*$")
EVENT_ID_RE: Final = re.compile(
    r"^(metric|log|alert|topology|config)-[A-Za-z0-9_.]+-[0-9]{6}$"
)

VALID_SOURCES: Final[frozenset[str]] = frozenset(
    {"metric", "log", "alert", "topology", "config"}
)

# Kubernetes generated-name suffixes, stripped *before* separator normalization.
#   Deployment pod:  <name>-<replicaset-hash>-<pod-hash>  e.g. front-end-6c4d8b9f8d-abcde
#   ReplicaSet:      <name>-<replicaset-hash>             e.g. front-end-6c4d8b9f8d
_POD_HASH_RE: Final = re.compile(r"-[0-9a-f]{8,10}-[a-z0-9]{5}$")
_RS_HASH_RE: Final = re.compile(r"-[0-9a-f]{8,10}$")

# Alias map applied *after* separators are collapsed to hyphens. Only the
# separator-free concatenations need entries here; underscore/dot/slash forms
# (e.g. "front_end", "queue_master") already resolve via the separator step.
ALIASES: Final[dict[str, str]] = {
    "frontend": "front-end",
    "cartsdb": "carts-db",
    "ordersdb": "orders-db",
    "userdb": "user-db",
    "cataloguedb": "catalogue-db",
    "queuemaster": "queue-master",
}

_SEPARATORS_RE: Final = re.compile(r"[\s_./]+")
_MULTI_HYPHEN_RE: Final = re.compile(r"-{2,}")


def normalize_component(raw: str) -> str:
    """Normalize a raw component string into a canonical ``component_id``.

    Pipeline: lowercase -> strip k8s pod-hash suffixes -> separators to
    hyphens -> alias map -> validate against ``COMPONENT_RE``.

    Raises:
        ValueError: if the input cannot be normalized to a valid component id.
    """
    if not isinstance(raw, str):
        raise ValueError(f"component must be a str, got {type(raw).__name__}")

    s = raw.strip().lower()
    if not s:
        raise ValueError("cannot normalize empty component string")

    # strip k8s pod-hash suffixes (deployment pod first, then bare replicaset)
    s = _POD_HASH_RE.sub("", s)
    s = _RS_HASH_RE.sub("", s)

    # separators -> hyphens; collapse repeats; trim edge hyphens
    s = _SEPARATORS_RE.sub("-", s)
    s = _MULTI_HYPHEN_RE.sub("-", s).strip("-")

    # alias map
    s = ALIASES.get(s, s)

    if not COMPONENT_RE.match(s):
        raise ValueError(f"cannot normalize {raw!r} -> {s!r} (fails {COMPONENT_RE.pattern})")
    return s


def make_event_id(source: str, component: str, seq: int) -> str:
    """Construct a contract-valid ``event_id``.

    The component is normalized, then hyphens are swapped for underscores so the
    middle segment satisfies the event-id regex (which forbids hyphens).

    Args:
        source: one of ``VALID_SOURCES``.
        component: any raw component string (normalized internally).
        seq: zero-padded to 6 digits; must be in ``[0, 999999]``.

    Raises:
        ValueError: on bad source, out-of-range seq, or a component that cannot
            be normalized / produces an invalid id.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}")
    if not isinstance(seq, int) or isinstance(seq, bool) or not (0 <= seq <= 999_999):
        raise ValueError(f"seq must be an int in [0, 999999], got {seq!r}")

    component_id = normalize_component(component)
    middle = component_id.replace("-", "_")
    event_id = f"{source}-{middle}-{seq:06d}"

    if not EVENT_ID_RE.match(event_id):
        raise ValueError(f"constructed invalid event_id {event_id!r}")
    return event_id


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "normalize" and len(argv) == 3:
        print(normalize_component(argv[2]))
        return 0
    if len(argv) == 5 and argv[1] == "event-id":
        print(make_event_id(argv[2], argv[3], int(argv[4])))
        return 0
    print(
        "usage:\n"
        "  normalize <raw-component>\n"
        "  event-id <source> <component> <seq>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
