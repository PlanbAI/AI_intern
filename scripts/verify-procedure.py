"""Независимая перепроверка критериев успеха ПОСЛЕ процедуры (без прогона шагов).

Сценарий: пользователь выполнил что-то вручную (или процедура уже прошла) —
проверяем только criteria, не трогая state (runs/ok) и статус процедуры.

ВАЖНО: провал верификации НЕ помечает процедуру «нерабочей» (status не меняется,
state не пишется) — это независимая сверка, решение принимает пользователь.

Как проверяются типы (машинно, не LLM):
  http_status — URL извлекается из команд шагов (curl http://...);
  exit_code   — повторный запуск ПОСЛЕДНЕГО шага (осторожно: для деструктивных
                процедур последний шаг может иметь эффект — используй --check-types);
  regex       — --file (лог) или вывод последнего шага.

Использование:
  python scripts/verify-procedure.py --procedure P002 --host server2 --bindings "HOST=server2;PORT=9090"
  python scripts/verify-procedure.py --procedure P002 --host server1 --check-types http_status
  python scripts/verify-procedure.py --procedure P002 --host server1 --file app.log
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import PROCEDURES_DIR, STATE_DIR, read_yaml  # noqa: E402
from run_procedure import PARAM_RE, URL_RE, load_bindings, substitute  # noqa: E402
from verify import check_exit_code, check_http_status, check_regex  # noqa: E402


def run_last_step(cmd: str, timeout: int) -> str:
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout)
        return (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return "[timeout]"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procedure", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--bindings", default="")
    ap.add_argument("--check-types", default="", help="только эти типы, через запятую")
    ap.add_argument("--file", default="", help="файл для regex-критериев (лог)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    proc_file = PROCEDURES_DIR / f"{args.procedure}.yaml"
    if not proc_file.exists():
        print(f"Процедура {args.procedure} не найдена.", file=sys.stderr)
        return 2
    proc = read_yaml(proc_file)
    criteria = proc.get("criteria", [])
    if not criteria:
        print("Критерии успеха не заданы — верифицировать нечего.", file=sys.stderr)
        return 1
    steps = proc.get("steps", [])

    bindings = load_bindings(args.procedure, args.host)
    for pair in args.bindings.split(";") if args.bindings else []:
        if "=" in pair:
            k, v = pair.split("=", 1)
            bindings[k.strip()] = v.strip()

    rendered = [substitute(s.get("command", ""), bindings)[0] for s in steps]
    missing: list[str] = []
    for s in steps:
        _, m = substitute(s.get("command", ""), bindings)
        missing.extend(m)
    if missing:
        print(f"Не заданы параметры: {sorted(set(missing))}", file=sys.stderr)
        return 2

    only = {t.strip() for t in args.check_types.split(",") if t.strip()}
    last_cmd = rendered[-1] if rendered else ""
    combined = run_last_step(last_cmd, args.timeout) if last_cmd else ""
    if args.file:
        try:
            combined += "\n" + Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Не удалось прочитать {args.file}: {exc}", file=sys.stderr)
            return 2

    results = []
    for c in criteria:
        ctype = c.get("type")
        value = c.get("value")
        if only and ctype not in only:
            continue
        if ctype == "http_status":
            url = None
            for cmd in rendered:
                m = URL_RE.search(cmd)
                if m:
                    url = m.group(0)
                    break
            if not url:
                results.append({"type": ctype, "expected": value, "actual": "no url",
                                "ok": False, "detail": "URL не найден в шагах"})
                continue
            r = check_http_status(int(value), url, args.timeout)
        elif ctype == "exit_code":
            if not last_cmd:
                results.append({"type": ctype, "expected": value, "actual": "no steps",
                                "ok": False})
                continue
            r = check_exit_code(int(value), last_cmd, args.timeout)
        elif ctype == "regex":
            r = check_regex(str(value), combined)
        else:
            results.append({"type": ctype, "expected": value, "actual": "unsupported",
                            "ok": False})
            continue
        results.append({"type": ctype, "expected": value,
                        "actual": r.get("actual"), "ok": r["ok"],
                        "detail": r.get("detail", "")})

    ok = all(r["ok"] for r in results)
    if args.json:
        print(json.dumps({"procedure": proc.get("id"), "host": args.host,
                          "criteria_results": results, "ok": ok},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== Верификация {proc.get('id')} @ {args.host} ===")
        for r in results:
            mark = "OK " if r["ok"] else "FAIL"
            print(f"  [{mark}] {r['type']}: ожидалось {r['expected']}, "
                  f"фактически {r.get('actual')}")
        print(f"  Итог: {'КРИТЕРИИ ВЫПОЛНЕНЫ' if ok else 'КРИТЕРИИ НЕ ВЫПОЛНЕНЫ'}")
        print("  (state/status не менялись — это независимая сверка)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
