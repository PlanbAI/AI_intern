"""Машинный верификатор критериев успеха (НЕ LLM).

Поддерживаемые типы (схема: schema/procedure.schema.json):
  exit_code   — выполнить команду, сравнить код возврата с value
  http_status — HTTP-запрос к URL, сравнить статус с value
  regex       — искать value (регэксп) в тексте (--text | --file)

Код возврата: 0 — критерий выполнен, 1 — не выполнен, 2 — ошибка вызова.

Использование:
  python scripts/verify.py --type exit_code --value 0 --command "python -c \"import sys; sys.exit(0)\""
  python scripts/verify.py --type http_status --value 200 --url http://127.0.0.1:8080/health
  python scripts/verify.py --type regex --value "status.: .ok." --file app.log
  python scripts/verify.py --type regex --value "started" --text "service started"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request


def check_exit_code(value: int, command: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=timeout)
        actual = proc.returncode
        return {"ok": actual == value, "actual": actual,
                "detail": (proc.stdout or "")[:500] + (proc.stderr or "")[:500]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "actual": "timeout", "detail": f"превышен таймаут {timeout}с"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "actual": "error", "detail": str(exc)}


def check_http_status(value: int, url: str, timeout: int) -> dict:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            actual = resp.status
            body = resp.read(4096).decode("utf-8", errors="replace")
        return {"ok": actual == value, "actual": actual,
                "detail": body[:400]}
    except urllib.error.HTTPError as exc:  # noqa: PLC2701
        return {"ok": exc.code == value, "actual": exc.code, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "actual": "error", "detail": str(exc)}


def check_regex(value: str, text: str) -> dict:
    try:
        m = re.search(value, text)
        return {"ok": bool(m), "actual": m.group(0) if m else "no match",
                "detail": f"регэксп: {value}"}
    except re.error as exc:
        return {"ok": False, "actual": "bad regex", "detail": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--type", required=True, choices=["exit_code", "http_status", "regex"])
    ap.add_argument("--value", required=True, help="ожидаемое значение (int или регэксп)")
    ap.add_argument("--command", default="", help="для exit_code")
    ap.add_argument("--url", default="", help="для http_status")
    ap.add_argument("--text", default="", help="для regex: текст напрямую")
    ap.add_argument("--file", default="", help="для regex: файл")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.type == "exit_code":
        if not args.command:
            print("--command обязателен для exit_code", file=sys.stderr)
            return 2
        result = check_exit_code(int(args.value), args.command, args.timeout)
    elif args.type == "http_status":
        if not args.url:
            print("--url обязателен для http_status", file=sys.stderr)
            return 2
        result = check_http_status(int(args.value), args.url, args.timeout)
    else:
        if args.file:
            try:
                args.text = Path(args.file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"Не удалось прочитать {args.file}: {exc}", file=sys.stderr)
                return 2
        if not args.text:
            print("--text или --file обязателен для regex", file=sys.stderr)
            return 2
        result = check_regex(args.value, args.text)

    result.update({"type": args.type, "expected": args.value})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mark = "OK" if result["ok"] else "FAIL"
        print(f"[{mark}] {args.type}: ожидалось {args.value}, фактически "
              f"{result['actual']}")
        if result.get("detail"):
            print(f"   detail: {result['detail'][:200]}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
