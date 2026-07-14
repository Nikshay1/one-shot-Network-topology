"""Explore a raw RE2-SS case directory and print its ACTUAL layout.

This runs BEFORE the adapter is written. The observed output drives
``data/README.md``; the adapter is then written against the README, never
against hardcoded assumptions the explorer contradicts.

    py -m backend.ingest.explore data/re2_ss/catalogue_cpu/1
    py -m backend.ingest.explore data/re2_ss/catalogue_cpu        # walks runs
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

_MAX_DEPTH = 3
_SAMPLE_ROWS = 2
_MAX_COLS_SHOWN = 60


def _print_tree(root: Path, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        return
    entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    for p in entries:
        indent = "  " * depth
        if p.is_dir():
            print(f"{indent}{p.name}/")
            _print_tree(p, depth + 1)
        else:
            size = p.stat().st_size
            print(f"{indent}{p.name}  ({size:,} bytes)")


def _describe_csv(path: Path) -> None:
    print(f"\n--- CSV: {path.name} ---")
    # csv module has a field-size cap; some log messages are huge JSON blobs.
    try:
        csv.field_size_limit(sys.maxsize)
    except OverflowError:  # pragma: no cover - platform dependent
        csv.field_size_limit(2**31 - 1)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            print("  (empty file)")
            return
        print(f"  columns: {len(header)}")
        shown = header[:_MAX_COLS_SHOWN]
        print(f"  header[:{_MAX_COLS_SHOWN}]: {shown}")
        if len(header) > _MAX_COLS_SHOWN:
            print(f"  ... (+{len(header) - _MAX_COLS_SHOWN} more columns)")
        for i, row in enumerate(reader):
            if i >= _SAMPLE_ROWS:
                break
            cells = [c if len(c) <= 60 else c[:57] + "..." for c in row[:_MAX_COLS_SHOWN]]
            print(f"  row[{i}][:{_MAX_COLS_SHOWN}]: {cells}")


def _describe_json(path: Path) -> None:
    print(f"\n--- JSON: {path.name} ---")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  (could not parse: {exc})")
        return
    if isinstance(data, dict):
        keys = list(data.keys())
        print(f"  top-level: dict with {len(keys)} keys")
        print(f"  keys[:20]: {keys[:20]}")
        if keys:
            first = data[keys[0]]
            print(f"  sample value (key {keys[0]!r}): "
                  f"{_truncate(json.dumps(first, default=str))}")
    elif isinstance(data, list):
        print(f"  top-level: list of {len(data)} items")
        if data:
            print(f"  item[0]: {_truncate(json.dumps(data[0], default=str))}")
    else:
        print(f"  top-level: {type(data).__name__} = {_truncate(str(data))}")


def _describe_text(path: Path) -> None:
    print(f"\n--- TEXT: {path.name} ---")
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    print(f"  content: {_truncate(content, 200)}")


def _truncate(s: str, n: int = 120) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 3] + "..."


def explore(root: Path) -> None:
    print("=" * 70)
    print(f"EXPLORING: {root}")
    print("=" * 70)
    print("\n[ FOLDER TREE (<=3 levels) ]")
    _print_tree(root)

    # Collect files recursively (bounded) and describe by type.
    files = sorted(p for p in root.rglob("*") if p.is_file())
    print(f"\n[ FILE DETAILS ]  ({len(files)} files)")
    seen_names: set[str] = set()
    for p in files:
        # Describe one representative of each distinct filename to avoid
        # re-printing identical schemas across repetition folders.
        if p.name in seen_names:
            continue
        seen_names.add(p.name)
        suffix = p.suffix.lower()
        if suffix == ".csv":
            _describe_csv(p)
        elif suffix == ".json":
            _describe_json(p)
        elif suffix in (".txt", ".log", ".md"):
            _describe_text(p)
        else:
            print(f"\n--- OTHER: {p.name}  ({p.stat().st_size:,} bytes) ---")


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m backend.ingest.explore <case_dir>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 1
    explore(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
