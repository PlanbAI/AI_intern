"""Фиксация результата процедуры, исполненной АГЕНТОМ (шаги type: agent, MCP).

Для bash-процедур результат фиксирует run_procedure.py. Для agent-процедур
(браузер, MCP-инструменты) шаги исполняет интерн в чате с подтверждениями —
этот скрипт записывает итог в state/<id>.yaml и телеметрию.

Использование (после исполнения интерном):
  python scripts/mark_result.py --procedure P003 --host local --ok
  python scripts/mark_result.py --procedure P003 --host local --fail --error "не удалось войти в Google"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_procedure import (  # noqa: E402
    SCRIPT_DIR, classify_procedure, load_bindings, telemetry, update_state,
)
from common import PROCEDURES_DIR, read_yaml  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procedure", required=True, help="id процедуры (Pxxx)")
    ap.add_argument("--host", default="local", help="хост (для agent-процедур — 'local')")
    ap.add_argument("--ok", action="store_true", help="результат успешен")
    ap.add_argument("--fail", action="store_true", help="результат провален")
    ap.add_argument("--error", default="", help="описание ошибки при --fail")
    ap.add_argument("--note", default="", help="заметка в телеметрию")
    args = ap.parse_args()

    if args.ok == args.fail:
        print("Укажите ровно один из флагов: --ok или --fail", file=sys.stderr)
        return 2

    ok = args.ok
    try:
        bindings = load_bindings(args.procedure, args.host)
    except Exception:  # noqa: BLE001
        bindings = {}
    # уровень — по классификации процедуры (log.py принимает только P0/P1/P2)
    try:
        proc = read_yaml(PROCEDURES_DIR / f"{args.procedure}.yaml")
        level = classify_procedure(proc).get("level", "P1")
    except Exception:  # noqa: BLE001
        level = "P1"
    update_state(args.procedure, args.host, ok, args.error, bindings)
    telemetry("mark", "run", "ok" if ok else "failed", args.procedure,
              level, 0, 0, args.note or args.error)
    print(f"Результат {args.procedure} @ {args.host}: "
          f"{'OK' if ok else 'FAIL'}" + (f" ({args.error})" if args.error else ""))
    print("Далее: при необходимости git-коммит.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())