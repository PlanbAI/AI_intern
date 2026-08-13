"""Классификатор уровня доступа P0/P1/P2 (правила, не LLM — rules-first).

P0  — read-only: только шаблоны allowlist (совпадают с permission-правилами opencode.json).
P2  — изменяющие состояние: scp/ssh с изменением, деплой, службы, права, диски, сеть.
P1  — всё остальное (по умолчанию). «Сомнение → выше» встроено: любой P2-сигнал
      поднимает уровень, P0 — только если ВСЕ команды в allowlist.

LLM-слой в скилле добавляет класс uncertain (расхождение в 3 прогонах) → уровень выше.

Использование:
  python scripts/classify.py --command "curl -sf http://x/health"
  python scripts/classify.py --procedure P002
  python scripts/classify.py --command "..." --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import PROCEDURES_DIR, read_yaml  # noqa: E402

# P0-allowlist (синхронизировать с permission-правилами opencode.json!)
P0_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(tail|grep|ps|df)\b", re.I),
    re.compile(r"^Get-(Content|Process|Service|PSDrive|ChildItem)\b", re.I),
    re.compile(r"^Select-String\b", re.I),
    re.compile(r"^curl\s+-(sf|fsS)\b", re.I),
    re.compile(r"^Test-NetConnection\b", re.I),
    re.compile(r"^git\s+(status|log|diff|show)\b", re.I),
    re.compile(r"^python\s+scripts\\(index|log|stats)\.py\b", re.I),
]

# P2-сигналы: изменение состояния системы/сервера
P2_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(scp|rsync|sftp)\b", re.I),
    re.compile(r"ssh\s+\S+.*\b(systemctl|service|restart|reboot|shutdown|install|rm|mv|cp)\b", re.I),
    re.compile(r"\b(systemctl|service)\s+\S+\s+(start|stop|restart|enable|disable)\b", re.I),
    re.compile(r"\b(Remove-Item|del|rmdir|rd|rm)\b", re.I),
    re.compile(r"\b(New-|Set-|Add-|Remove-|Start-|Stop-|Restart-)\w*\b", re.I),
    re.compile(r"\b(reboot|shutdown|halt)\b", re.I),
    re.compile(r"\b(chmod|chown|icacls)\b", re.I),
    re.compile(r"\b(diskpart|format|mkfs|fdisk|reg\s+add|netsh|iptables|ufw)\b", re.I),
    re.compile(r"\b(useradd|usermod|userdel|net\s+user|New-LocalUser)\b", re.I),
    re.compile(r"\b(deploy|docker\s+(build|run|push)|kubectl\s+apply)\b", re.I),
    re.compile(r"\b(Invoke-Expression|iex)\b", re.I),
    re.compile(r"curl\s+\S+\s+(-o|-O|--output|--remote-name)", re.I),
    re.compile(r"tar\s+-x", re.I),
]


def classify_command(cmd: str) -> dict:
    """Классификация одной команды."""
    if not cmd.strip():
        return {"level": "P1", "reason": "пустая команда", "signals": []}
    for p in P2_PATTERNS:
        if p.search(cmd):
            return {"level": "P2", "reason": f"сигнал изменения состояния: {p.pattern[:60]}",
                    "signals": [p.pattern]}
    for p in P0_PATTERNS:
        if p.match(cmd):
            return {"level": "P0", "reason": f"read-only allowlist: {p.pattern[:60]}",
                    "signals": [p.pattern]}
    return {"level": "P1", "reason": "нет сигналов — по умолчанию P1", "signals": []}


def classify_procedure(proc: dict) -> dict:
    """Уровень процедуры: P2 если есть хоть один P2-шаг; P0 только если ВСЕ шаги P0
    и заданы критерии успеха; иначе P1."""
    steps = proc.get("steps", [])
    if not steps:
        return {"level": "P1", "reason": "нет шагов", "signals": []}
    levels = [classify_command(s.get("command", ""))["level"] for s in steps]
    if "P2" in levels:
        return {"level": "P2", "reason": f"есть P2-шаги ({levels.count('P2')} шт.)",
                "signals": ["P2"]}
    if all(l == "P0" for l in levels) and proc.get("criteria"):
        return {"level": "P0", "reason": "все шаги read-only + критерии заданы",
                "signals": ["P0"]}
    return {"level": "P1", "reason": f"смешанные/неполные сигналы ({levels})", "signals": levels}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--command", default="", help="одна команда")
    ap.add_argument("--procedure", default="", help="id процедуры (например P002)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.command:
        result = classify_command(args.command)
    elif args.procedure:
        path = PROCEDURES_DIR / f"{args.procedure}.yaml"
        if not path.exists():
            print(f"Процедура {args.procedure} не найдена.", file=sys.stderr)
            return 2
        result = classify_procedure(read_yaml(path))
    else:
        print("Укажи --command или --procedure.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Уровень: {result['level']} — {result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
