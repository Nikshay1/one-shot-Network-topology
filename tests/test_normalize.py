"""Tests for the single source of truth: normalize_component + make_event_id."""

from __future__ import annotations

import pytest

from backend.ingest.normalize import make_event_id, normalize_component


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("front-end", "front-end"),
        ("Front-End", "front-end"),
        ("FRONTEND", "front-end"),
        ("frontend", "front-end"),
        ("front_end", "front-end"),
        ("front end", "front-end"),
        ("carts", "carts"),
        ("carts_db", "carts-db"),
        ("cartsdb", "carts-db"),
        ("CartsDB", "carts-db"),
        ("orders_db", "orders-db"),
        ("ordersdb", "orders-db"),
        ("user_db", "user-db"),
        ("userdb", "user-db"),
        ("catalogue.db", "catalogue-db"),
        ("catalogue_db", "catalogue-db"),
        ("cataloguedb", "catalogue-db"),
        ("queue_master", "queue-master"),
        ("payment", "payment"),
        ("orders/db", "orders-db"),
        ("  carts--db  ", "carts-db"),
    ],
)
def test_normalize_basic_and_aliases(raw: str, expected: str) -> None:
    assert normalize_component(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("carts-7c4d8b9f8d-xz9k2", "carts"),
        ("front-end-6c4d8b9f8d-abcde", "front-end"),
        ("catalogue-db-5f6d7c8b9a-q4wer", "catalogue-db"),
        ("orders-db-9a8b7c6d5e-11abc", "orders-db"),
        ("front-end-6c4d8b9f8d", "front-end"),
    ],
)
def test_normalize_strips_k8s_pod_hashes(raw: str, expected: str) -> None:
    assert normalize_component(raw) == expected


@pytest.mark.parametrize(
    "pod,expected",
    [
        # Real pod names lifted verbatim from RE2-SS/*/pod-node-1.csv.
        ("carts-795df7fd79-7x8z8", "carts"),
        ("carts-db-5d88c7dddc-x4qpf", "carts-db"),
        ("catalogue-6999fd64d9-pjhhn", "catalogue"),
        ("catalogue-db-554cbfd749-sbwkr", "catalogue-db"),
        ("front-end-75b898f665-t56zp", "front-end"),
        ("loadgenerator-564d55cfcb-f7dt4", "loadgenerator"),
        ("orders-688f957476-2hzmk", "orders"),
        ("orders-db-7c75b66575-cp4wr", "orders-db"),
        ("payment-766c44b5dc-g7mln", "payment"),
        ("queue-master-5f47847cd-lnq44", "queue-master"),
        ("rabbitmq-5dcbdd5997-tbxhz", "rabbitmq"),
        ("session-db-6f9cdc9746-rrf9n", "session-db"),
        ("shipping-6d4449fd75-4vjb7", "shipping"),
        ("user-77c9d4d7cf-xdp9d", "user"),
        ("user-db-57c446cdbc-hz7nc", "user-db"),
    ],
)
def test_normalize_real_re2ss_pod_names(pod: str, expected: str) -> None:
    """normalize_component must canonicalize every real RE2-SS pod name."""
    assert normalize_component(pod) == expected


def test_normalize_real_re2ss_metric_columns_are_idempotent() -> None:
    """Component tokens as they appear in simple_metrics.csv columns are already
    canonical and must pass through unchanged."""
    for comp in [
        "carts", "carts-db", "catalogue", "catalogue-db", "front-end",
        "orders", "orders-db", "payment", "queue-master", "rabbitmq",
        "rabbitmq-exporter", "session-db", "shipping", "user", "user-db",
    ]:
        assert normalize_component(comp) == comp


@pytest.mark.parametrize("garbage", ["", "   ", "!!!", "a b!c", "@@@", "..", "__"])
def test_normalize_raises_on_garbage(garbage: str) -> None:
    with pytest.raises(ValueError):
        normalize_component(garbage)


def test_normalize_rejects_non_str() -> None:
    with pytest.raises(ValueError):
        normalize_component(None)  # type: ignore[arg-type]


def test_normalize_is_idempotent() -> None:
    for raw in ["Front-End", "cartsdb", "catalogue.db", "queue_master"]:
        once = normalize_component(raw)
        assert normalize_component(once) == once


@pytest.mark.parametrize(
    "source,component,seq,expected",
    [
        ("metric", "front-end", 1, "metric-front_end-000001"),
        ("log", "catalogue-db", 42, "log-catalogue_db-000042"),
        ("alert", "carts", 0, "alert-carts-000000"),
        ("config", "cataloguedb", 7, "config-catalogue_db-000007"),
        ("topology", "front_end", 999999, "topology-front_end-999999"),
    ],
)
def test_make_event_id(source: str, component: str, seq: int, expected: str) -> None:
    assert make_event_id(source, component, seq) == expected


def test_make_event_id_invalid_source() -> None:
    with pytest.raises(ValueError):
        make_event_id("trace", "front-end", 1)


@pytest.mark.parametrize("seq", [-1, 1_000_000, True])
def test_make_event_id_bad_seq(seq: int) -> None:
    with pytest.raises(ValueError):
        make_event_id("metric", "front-end", seq)


def test_make_event_id_matches_contract_regex() -> None:
    import re

    from backend.ingest.normalize import EVENT_ID_RE

    eid = make_event_id("metric", "front-end", 3)
    assert EVENT_ID_RE.match(eid)
    assert re.match(
        r"^(metric|log|alert|topology|config)-[A-Za-z0-9_.]+-[0-9]{6}$", eid
    )
