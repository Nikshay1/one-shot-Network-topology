"""EventStore — DuckDB + Parquet event store.

Parquet is partitioned on disk by ``case_id`` / ``source``; DuckDB is the query
engine. Runnable/testable via CLI without the API server.

    py -m backend.ingest.store info data/parquet
    py -m backend.ingest.store events data/parquet <case_id> --source metric
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import networkx as nx
import polars as pl

from backend.models import EventEnvelope

# Physical/queryable columns written per event. payload_json carries the full
# typed payload so an EventEnvelope can always be reconstructed losslessly.
_COLUMNS = [
    "event_id",
    "case_id",
    "source",
    "component_id",
    "ts",
    "metric_name",
    "value",
    "level",
    "message",
    "template_id",
    "payload_json",
]

_SCHEMA: dict[str, Any] = {
    "event_id": pl.Utf8,
    "case_id": pl.Utf8,
    "source": pl.Utf8,
    "component_id": pl.Utf8,
    "ts": pl.Float64,
    "metric_name": pl.Utf8,
    "value": pl.Float64,
    "level": pl.Utf8,
    "message": pl.Utf8,
    "template_id": pl.Utf8,
    "payload_json": pl.Utf8,
}


def _event_row(ev: EventEnvelope) -> dict[str, Any]:
    """Flatten an EventEnvelope into a store row (typed common cols + payload)."""
    p = ev.payload
    row: dict[str, Any] = {
        "event_id": ev.event_id,
        "case_id": ev.case_id,
        "source": ev.source,
        "component_id": ev.component_id,
        "ts": ev.ts,
        "metric_name": None,
        "value": None,
        "level": None,
        "message": None,
        "template_id": None,
        "payload_json": p.model_dump_json(),
    }
    kind = p.kind
    if kind == "metric":
        row["metric_name"] = p.name
        row["value"] = float(p.value)
    elif kind == "log":
        row["level"] = p.level
        row["message"] = p.message
        row["template_id"] = p.template_id
    elif kind == "alert":
        row["metric_name"] = p.name
        row["value"] = float(p.severity)
    elif kind == "config":
        row["metric_name"] = p.key
    elif kind == "topology":
        row["message"] = f"{p.relation}:{p.target_component_id}"
    return row


class EventStore:
    """Append/query event store backed by partitioned Parquet + DuckDB."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- write ------------------------------------------------------------
    def write_case(self, bundle: "CaseBundle") -> int:  # noqa: F821 (fwd ref)
        """Write all events of a case, partitioned by case_id/source. Returns count."""
        rows = [_event_row(ev) for ev in bundle.events]
        if not rows:
            return 0
        df = pl.DataFrame(rows, schema=_SCHEMA)
        written = 0
        for (case_id, source), part in df.group_by(["case_id", "source"], maintain_order=True):
            out_dir = self.root / f"case_id={case_id}" / f"source={source}"
            out_dir.mkdir(parents=True, exist_ok=True)
            part.write_parquet(out_dir / "events.parquet")
            written += part.height
        return written

    # -- query ------------------------------------------------------------
    def _glob(self) -> str:
        return str(self.root / "**" / "*.parquet")

    def _has_data(self) -> bool:
        return any(self.root.rglob("*.parquet"))

    def events(
        self,
        case_id: str,
        source: str | None = None,
        component_id: str | None = None,
        t0: float | None = None,
        t1: float | None = None,
    ) -> pl.DataFrame:
        """Query events for a case with optional source/component/time filters."""
        if not self._has_data():
            return pl.DataFrame(schema=_SCHEMA)
        clauses = ["case_id = ?"]
        params: list[Any] = [case_id]
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if component_id is not None:
            clauses.append("component_id = ?")
            params.append(component_id)
        if t0 is not None:
            clauses.append("ts >= ?")
            params.append(float(t0))
        if t1 is not None:
            clauses.append("ts <= ?")
            params.append(float(t1))
        where = " AND ".join(clauses)
        query = (
            f"SELECT {', '.join(_COLUMNS)} FROM read_parquet(?, union_by_name=true) "
            f"WHERE {where} ORDER BY ts, event_id"
        )
        con = duckdb.connect()
        try:
            return con.execute(query, [self._glob(), *params]).pl()
        finally:
            con.close()

    def resolve(self, event_ids: list[str]) -> bool:
        """True iff every id in event_ids exists in the store."""
        wanted = set(event_ids)
        if not wanted:
            return True
        if not self._has_data():
            return False
        con = duckdb.connect()
        try:
            con.register("wanted_ids", pl.DataFrame({"event_id": list(wanted)}))
            found = con.execute(
                "SELECT COUNT(DISTINCT p.event_id) FROM read_parquet(?, union_by_name=true) p "
                "SEMI JOIN wanted_ids w ON p.event_id = w.event_id",
                [self._glob()],
            ).fetchone()[0]
            return int(found) == len(wanted)
        finally:
            con.close()

    # -- topology (node-link JSON alongside the event partitions) ---------
    def _topology_path(self, case_id: str) -> Path:
        return self.root / f"case_id={case_id}" / "topology.json"

    def write_topology(self, case_id: str, graph: nx.DiGraph) -> Path:
        p = self._topology_path(case_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = nx.node_link_data(graph, edges="links")
        except TypeError:  # older networkx
            data = nx.node_link_data(graph)
        p.write_text(json.dumps(data, default=str), encoding="utf-8")
        return p

    def load_topology(self, case_id: str) -> nx.DiGraph | None:
        p = self._topology_path(case_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        try:
            return nx.node_link_graph(data, edges="links")
        except TypeError:  # older networkx
            return nx.node_link_graph(data)

    # -- targeted rewrite (e.g. fill template_id back onto log events) -----
    def replace_source(self, case_id: str, source: str, df: pl.DataFrame) -> int:
        """Overwrite the parquet for one (case_id, source) partition."""
        out_dir = self.root / f"case_id={case_id}" / f"source={source}"
        out_dir.mkdir(parents=True, exist_ok=True)
        df.select(_COLUMNS).write_parquet(out_dir / "events.parquet")
        return df.height

    def get_by_ids(self, event_ids: list[str], case_id: str) -> pl.DataFrame:
        """Return rows for the given event_ids WITHIN ONE CASE.

        `case_id` is mandatory, and that is the whole point. event_id is only unique
        *inside* a case — the generator numbers events per case, so
        `metric-catalogue-000023` exists in all 26 cases in `data/parquet`. This
        method used to accept ids alone and semi-join on event_id, documented as
        "(any case)": one lookup returned 26 rows from 26 unrelated cases.

        That fed four separate defects — the agent's `get_events` showed foreign
        telemetry as evidence, `file_finding` resolved citations against cases that
        weren't under investigation, the challenger's "cited event must EXIST" check
        passed on events from other cases, and the SSE catch-up flush streamed
        duplicate/foreign `event_ingested` frames. Requiring the scope at the type
        level is what stops a fifth one being written.
        """
        wanted = list(dict.fromkeys(event_ids))
        if not wanted or not self._has_data():
            return pl.DataFrame(schema=_SCHEMA)
        con = duckdb.connect()
        try:
            con.register("wanted_ids", pl.DataFrame({"event_id": wanted}))
            return con.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM read_parquet(?, union_by_name=true) p "
                "SEMI JOIN wanted_ids w ON p.event_id = w.event_id "
                "WHERE p.case_id = ?",
                [self._glob(), case_id],
            ).pl()
        finally:
            con.close()

    def case_summaries(self) -> list[dict]:
        """(case_id, n_events, n_components) for every case — ONE scan, not one per case.

        `GET /cases` lists ~26 cases; counting them individually would re-scan the
        whole store 26 times (the real RE2-SS case alone is ~186k rows).
        """
        if not self._has_data():
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT case_id, COUNT(*) AS n_events, "
                "COUNT(DISTINCT component_id) AS n_components "
                "FROM read_parquet(?, union_by_name=true) GROUP BY case_id ORDER BY case_id",
                [self._glob()],
            ).fetchall()
        finally:
            con.close()
        return [{"case_id": c, "n_events": int(n), "n_components": int(k)} for c, n, k in rows]

    def count(self, case_id: str | None = None) -> int:
        if not self._has_data():
            return 0
        con = duckdb.connect()
        try:
            if case_id is None:
                q, params = "SELECT COUNT(*) FROM read_parquet(?, union_by_name=true)", [self._glob()]
            else:
                q = "SELECT COUNT(*) FROM read_parquet(?, union_by_name=true) WHERE case_id = ?"
                params = [self._glob(), case_id]
            return int(con.execute(q, params).fetchone()[0])
        finally:
            con.close()


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_info = sub.add_parser("info", help="row counts")
    p_info.add_argument("root")
    p_ev = sub.add_parser("events", help="query events")
    p_ev.add_argument("root")
    p_ev.add_argument("case_id")
    p_ev.add_argument("--source", default=None)
    p_ev.add_argument("--component_id", default=None)
    args = ap.parse_args(argv[1:])

    store = EventStore(args.root)
    if args.cmd == "info":
        print(f"root={store.root}  total_rows={store.count()}")
        return 0
    if args.cmd == "events":
        df = store.events(args.case_id, source=args.source, component_id=args.component_id)
        print(f"rows={df.height}")
        with pl.Config(tbl_rows=10, fmt_str_lengths=60):
            print(df.head(10))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
