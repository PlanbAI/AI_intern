"""Агрегация телеметрии: success rate, срез по событиям/уровням, стоимость, latency.

Пример:
  python scripts/stats.py                 # сводка по всем месяцам
  python scripts/stats.py --session s1    # по конкретной сессии
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import STATE_DIR, TELEMETRY_DIR  # noqa: E402
from common import read_yaml  # noqa: E402


def load_events(session: str | None = None) -> list[dict]:
    events = []
    for path in sorted(TELEMETRY_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session and ev.get("session") != session:
                continue
            events.append(ev)
    return events


def p95(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default="")
    args = ap.parse_args()

    events = load_events(args.session or None)
    if not events:
        print("Телеметрии нет.")
        return 0

    by_event: dict[str, list[dict]] = {}
    for ev in events:
        by_event.setdefault(ev.get("event", "?"), []).append(ev)

    print(f"Всего событий: {len(events)}")
    print(f"{'Событие':<10} {'Всего':>6} {'OK':>6} {'Failed':>8} {'Успех%':>8}")
    for event, evs in sorted(by_event.items()):
        ok = sum(1 for e in evs if e.get("status") == "ok")
        failed = sum(1 for e in evs if e.get("status") == "failed")
        decided = ok + failed
        rate = round(ok / decided * 100) if decided else "-"
        print(f"{event:<10} {len(evs):>6} {ok:>6} {failed:>8} {str(rate):>8}")

    lat = [e.get("ms", 0) for e in events if e.get("ms")]
    cost = sum(e.get("cost", 0.0) for e in events)
    tokens = sum(e.get("tokens", 0) for e in events)
    steps = sum(e.get("steps", 0) for e in events)
    print(f"\nLatency p95: {p95(lat)} мс (событий с ms: {len(lat)})")
    print(f"Токенов всего: {tokens}, шагов всего: {steps}")
    print(f"Стоимость всего: ${cost:.4f}")

    print("\n=== Процедуры по state (runs/ok per host) ===")
    for path in sorted(STATE_DIR.glob("P*.yaml")):
        state = read_yaml(path)
        for host, h in sorted(state.get("hosts", {}).items()):
            runs, ok = h.get("runs", 0), h.get("ok", 0)
            rate = f"{ok/runs*100:.0f}%" if runs else "-"
            flag = " ⚠️ ДЕГРАДАЦИЯ" if runs >= 3 and ok / runs < 0.66 else ""
            print(f"  {state.get('procedure_id', path.stem)} @ {host}: "
                  f"runs={runs} ok={ok} ({rate}){flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
