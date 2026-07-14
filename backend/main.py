"""Server entry point.

    py -m backend.main                 # or: make run
    py -m backend.main --port 8001 --reload
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backend.main", description="run the VERDICT API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv[1:])

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    uvicorn.run("backend.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
