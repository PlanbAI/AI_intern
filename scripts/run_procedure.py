"""Раннер процедур: исполнение по уровням P0/P1/P2 с проверками и лимитами.

Поток:
  1. процедура + биндинги из state/<id>.yaml (per-host), переопределение через --bindings;
  2. подстановка {{ПАРАМЕТР}} (недостающие биндинги — ОТКАЗ с перечнем);
  3. классификация уровня: --level auto (classify.py) или явно P0/P1/P2;
     P0 — только команды из allowlist (иначе отказ); P1 — подтверждение каждого
     шага (или --approve-all); P2 — сессионная авторизация без подсказок;
  4. лимиты: --max-steps, --timeout (на шаг);
  5. проверка step.check после каждого шага; в конце — глобальные criteria;
  6. обновление state (runs/ok/last_result) + телеметрия log.py.

Коды возврата: 0 успех, 1 проверки/критерии не прошли, 2 ошибка вызова,
3 отклонено/не подтверждено, 4 шаги не выполнены (лимиты/защита).

Использование:
  python scripts/run_procedure.py --procedure P002 --host server2 --bindings "PORT=9090"
  python scripts/run_procedure.py --procedure P002 --host server2 --level P0 --dry-run
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

from common import (  # noqa: E402
    MEMORY_DIR,
    PROCEDURES_DIR,
    STATE_DIR,
    MemoryLock,
    atomic_write_text,
    now_iso,
    read_yaml,
)
from classify import P0_PATTERNS, classify_procedure  # noqa: E402

PARAM_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")
URL_RE = re.compile(r"https?://[^\s\"'|]+", re.I)
SCRIPT_DIR = Path(__file__).resolve().parent


def load_bindings(pid: str, host: str) -> dict:
    state_file = STATE_DIR / f"{pid}.yaml"
    if not state_file.exists():
        return {}
    state = read_yaml(state_file)
    return state.get("hosts", {}).get(host, {}).get("bindings", {}) or {}


def substitute(cmd: str, bindings: dict) -> tuple[str, list[str]]:
    missing: list[str] = []
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in bindings:
            return str(bindings[name])
        missing.append(name)
        return m.group(0)
    out = PARAM_RE.sub(repl, cmd)
    return out, missing


def run_step(command: str, timeout: int) -> dict:
    start = time.monotonic()
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=timeout)
        return {"exit_code": proc.returncode,
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:2000],
                "ms": int((time.monotonic() - start) * 1000)}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s",
                "ms": int((time.monotonic() - start) * 1000)}


def check_step(check: dict, result: dict, command: str, timeout: int) -> dict:
    ctype, value = check.get("type"), check.get("value")
    if ctype == "exit_code":
        return {"type": ctype, "expected": value, "actual": result["exit_code"],
                "ok": result["exit_code"] == value}
    if ctype == "http_status":
        m = URL_RE.search(command)
        if m:
            try:
                with urllib.request.urlopen(urllib.request.Request(m.group(0)), timeout=timeout) as resp:
                    actual = resp.status
                return {"type": ctype, "expected": value, "actual": actual,
                        "ok": actual == value}
            except urllib.error.HTTPError as exc:  # noqa: PLC2701
                return {"type": ctype, "expected": value, "actual": exc.code,
                        "ok": exc.code == value}
            except Exception as exc:  # noqa: BLE001
                return {"type": ctype, "expected": value, "actual": "error", "ok": False,
                        "detail": str(exc)}
        # нет URL в команде: интерпретируем через код возврата (curl -sf => 0 на 2xx)
        ok = result["exit_code"] == 0
        return {"type": ctype, "expected": value,
                "actual": result["exit_code"], "ok": ok,
                "detail": "URL не найден в команде — по коду возврата"}
    if ctype == "regex":
        text = result.get("stdout", "") + result.get("stderr", "")
        return {"type": ctype, "expected": value,
                "actual": "match" if re.search(str(value), text) else "no match",
                "ok": bool(re.search(str(value), text))}
    return {"type": ctype, "expected": value, "actual": "unsupported", "ok": False,
            "detail": f"неизвестный тип проверки {ctype}"}


def check_criteria(criteria: list, evidence: dict, timeout: int) -> list[dict]:
    results = []
    for c in criteria:
        ctype, value = c.get("type"), c.get("value")
        if ctype == "exit_code":
            actual = evidence.get("last_exit", None)
            results.append({"type": ctype, "expected": value, "actual": actual,
                            "ok": actual == value})
        elif ctype == "http_status":
            actual = evidence.get("http_status", None)
            results.append({"type": ctype, "expected": value, "actual": actual,
                            "ok": actual == value})
        elif ctype == "regex":
            text = evidence.get("combined_output", "")
            results.append({"type": ctype, "expected": value,
                            "actual": "match" if re.search(str(value), text) else "no match",
                            "ok": bool(re.search(str(value), text))})
        else:
            results.append({"type": ctype, "expected": value, "actual": "unsupported",
                            "ok": False})
    return results


def update_state(pid: str, host: str, ok: bool, error: str, bindings: dict) -> None:
    state_file = STATE_DIR / f"{pid}.yaml"
    state = read_yaml(state_file) if state_file.exists() else {
        "schema_version": 1, "procedure_id": pid, "hosts": {}}
    h = state["hosts"].setdefault(host, {"bindings": {}, "runs": 0, "ok": 0,
                                         "last_result": "unknown", "last_error": "",
                                         "last_run_at": ""})
    h["bindings"] = bindings
    h["runs"] += 1
    if ok:
        h["ok"] += 1
    h["last_result"] = "ok" if ok else "failed"
    h["last_error"] = error if not ok else ""
    h["last_run_at"] = now_iso()
    with MemoryLock(timeout_sec=10):
        atomic_write_text(state_file, json.dumps(state, ensure_ascii=False, indent=2))


def telemetry(session: str, event: str, status: str, pid: str, level: str,
              steps: int, ms: int, note: str) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "log.py"), "--session", session,
         "--event", event, "--status", status, "--procedure", pid,
         "--level", level, "--steps", str(steps), "--ms", str(ms), "--note", note],
        capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procedure", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--bindings", default="", help='доп. биндинги: "PORT=9090;USER=deploy"')
    ap.add_argument("--level", default="auto", choices=["auto", "P0", "P1", "P2"])
    ap.add_argument("--max-steps", type=int, default=0, help="0 = из процедуры")
    ap.add_argument("--timeout", type=int, default=0, help="0 = из процедуры (на шаг)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve-all", action="store_true", help="без подсказок (P1/P2)")
    ap.add_argument("--session", default="auto")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    proc_file = PROCEDURES_DIR / f"{args.procedure}.yaml"
    if not proc_file.exists():
        print(f"Процедура {args.procedure} не найдена.", file=sys.stderr)
        return 2
    proc = read_yaml(proc_file)
    steps = proc.get("steps", [])
    criteria = proc.get("criteria", [])
    max_steps = args.max_steps or proc.get("max_steps", 20)
    timeout = args.timeout or proc.get("timeout_seconds", 600)
    session = args.session if args.session != "auto" else f"run-{args.procedure}-{int(time.time())}"

    if len(steps) > max_steps:
        print(f"Шагов {len(steps)} > лимита {max_steps} — отказ.", file=sys.stderr)
        return 4

    bindings = load_bindings(args.procedure, args.host)
    for pair in args.bindings.split(";") if args.bindings else []:
        if "=" in pair:
            k, v = pair.split("=", 1)
            bindings[k.strip()] = v.strip()

    # подстановка параметров и проверка полноты
    rendered: list[tuple[dict, str]] = []
    all_missing: list[str] = []
    agent_steps = [s for s in steps if s.get("type", "bash") == "agent"]
    if agent_steps:
        print(f"Процедура содержит agent-шаги ({len(agent_steps)} шт.) — их исполняет "
              f"интерн в чате (MCP/браузер), а не этот раннер. Исполните шаги вручную и "
              f"зафиксируйте результат: python scripts/mark_result.py "
              f"--procedure {proc.get('id')} --host {args.host} --ok|--fail",
              file=sys.stderr)
        return 3
    for s in steps:
        cmd, missing = substitute(s.get("command", ""), bindings)
        all_missing.extend(missing)
        rendered.append((s, cmd))
    if all_missing:
        print(f"Не заданы параметры: {sorted(set(all_missing))}. "
              f"Биндинги: {bindings}", file=sys.stderr)
        return 2

    # уровень
    if args.level == "auto":
        level = classify_procedure(proc)["level"]
        note_level = f"auto→{level}"
    else:
        level = args.level
        note_level = level

    if level == "P0":
        for s, cmd in rendered:
            if not any(p.match(cmd) for p in P0_PATTERNS):
                print(f"P0, но команда вне allowlist — отказ (уровень должен быть выше): "
                      f"{cmd}", file=sys.stderr)
                return 4

    if args.dry_run:
        print(f"[dry-run] {proc.get('id')} '{proc.get('title')}' | уровень {note_level} "
              f"| шагов {len(rendered)} | лимит шагов {max_steps} | таймаут {timeout}с")
        for s, cmd in rendered:
            ck = f" | check {s.get('check', {})}" if s.get("check") else ""
            print(f"  {s['order']}. {cmd}{ck}")
        if criteria:
            print("  criteria:", json.dumps(criteria, ensure_ascii=False))
        return 0

    # исполнение
    start = time.monotonic()
    report = {"procedure": proc.get("id"), "title": proc.get("title"), "host": args.host,
              "level": note_level, "session": session, "steps": [], "criteria_results": [],
              "ok": False, "aborted": False}
    evidence = {"last_exit": None, "http_status": None, "combined_output": ""}

    for s, cmd in rendered:
        if level == "P1" and not args.approve_all:
            answer = input(f"[P1] Выполнить: {cmd}\n  (y=да / n=нет / q=прервать) > ")
            if answer.lower().startswith("q"):
                report["aborted"] = True
                break
            if not answer.lower().startswith("y"):
                report["aborted"] = True
                break
        step_result = run_step(cmd, timeout)
        check = None
        if s.get("check"):
            check = check_step(s["check"], step_result, cmd, timeout)
            if not check["ok"]:
                report["steps"].append({"order": s["order"], "command": cmd,
                                        "check": check, "step_result": step_result})
                report["aborted"] = False
                update_state(args.procedure, args.host, False,
                             f"шаг {s['order']}: {check.get('actual')}", bindings)
                telemetry(session, "run", "failed", args.procedure, level,
                          len(report["steps"]), int((time.monotonic() - start) * 1000),
                          f"шаг {s['order']}: {check.get('actual')}")
                report["ok"] = False
                _print_report(report, args.json)
                return 1
        evidence["last_exit"] = step_result["exit_code"]
        if check and check["type"] == "http_status":
            evidence["http_status"] = check["actual"]
        evidence["combined_output"] += step_result["stdout"] + step_result["stderr"]
        report["steps"].append({"order": s["order"], "command": cmd, "check": check,
                                "step_result": step_result})

    if report["aborted"]:
        print("Прервано пользователем.", file=sys.stderr)
        telemetry(session, "run", "failed", args.procedure, level, len(report["steps"]),
                  int((time.monotonic() - start) * 1000), "aborted by user")
        return 3

    report["criteria_results"] = check_criteria(criteria, evidence, timeout)
    all_ok = all(c["ok"] for c in report["criteria_results"])
    report["ok"] = all_ok
    update_state(args.procedure, args.host, all_ok,
                 "criteria failed" if not all_ok else "", bindings)
    telemetry(session, "run", "ok" if all_ok else "failed", args.procedure, level,
              len(report["steps"]), int((time.monotonic() - start) * 1000),
              "criteria ok" if all_ok else "criteria failed")
    _print_report(report, args.json)
    return 0 if all_ok else 1


def _print_report(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(f"\n=== Отчёт: {report['procedure']} '{report['title']}' @ {report['host']} "
          f"({report['level']}) ===")
    for st in report["steps"]:
        ck = st.get("check")
        mark = "OK " if not ck or ck["ok"] else "FAIL"
        print(f"  [{mark}] {st['order']}. {st['command'][:90]}")
    for c in report["criteria_results"]:
        mark = "OK " if c["ok"] else "FAIL"
        print(f"  criteria [{mark}] {c['type']}: ожидалось {c['expected']}, "
              f"фактически {c.get('actual')}")
    print(f"  Итог: {'УСПЕХ' if report['ok'] else 'НЕУСПЕХ'}")


if __name__ == "__main__":
    raise SystemExit(main())
