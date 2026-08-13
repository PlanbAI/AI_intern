"""Запись события телеметрии в agent-memory/telemetry/<ГГГГММ>.jsonl (атомарно).

Пример:
  python scripts/log.py --session s1 --event run --procedure P001 --status ok --level P1 --steps 4 --ms 1200 --cost 0.004 --note "health 200"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import TELEMETRY_DIR, MemoryLock, now_iso  # noqa: E402

EVENTS = {"learn", "run", "verify", "retrieve", "modify", "question", "error"}
STATUSES = {"ok", "failed", "unknown", "pending"}
LEVELS = {"P0", "P1", "P2"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", required=True)
    ap.add_argument("--event", required=True, choices=sorted(EVENTS))
    ap.add_argument("--procedure", default="")
    ap.add_argument("--status", default="unknown", choices=sorted(STATUSES))
    ap.add_argument("--level", default="", choices=sorted(LEVELS))
    ap.add_argument("--steps", type=int, default=0)
    ap.add_argument("--tokens", type=int, default=0)
    ap.add_argument("--cost", type=float, default=0.0)
    ap.add_argument("--ms", type=int, default=0)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    event = {
        "ts": now_iso(),
        "session": args.session,
        "event": args.event,
        "procedure": args.procedure,
        "status": args.status,
        "level": args.level,
        "steps": args.steps,
        "tokens": args.tokens,
        "cost": args.cost,
        "ms": args.ms,
        "note": args.note,
    }
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    month_file = TELEMETRY_DIR / (now_iso()[:7] + ".jsonl")
    with MemoryLock(timeout_sec=10):
        with open(month_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"log: {month_file.name} += {event['event']}/{event['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
