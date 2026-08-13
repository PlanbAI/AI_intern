"""Детерминированная разметка переменных в командах (правила, не LLM).

Помощник для LLM-разметки: даёт базовые сигналы с уверенностью (rules-first,
по рекомендации AI-специалиста). LLM добавляет класс «не уверен» и
батч-подтверждение пользователя остаётся обязательным.

Использование:
  python scripts/param_detect.py --cmd "scp app.tar.gz deploy@server1:/opt/app"
  python scripts/param_detect.py --file sessions/session.json   # {"actions":[{"cmd":...}]}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import read_json, read_yaml  # noqa: E402

# (regex, type, confidence, reason)
RULES: list[tuple[re.Pattern, str, float, str]] = [
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "host", 0.90, "IPv4-адрес"),
    (re.compile(r"\b(?:server|srv|prod|production|staging|test|dev|node)[a-z0-9-]*\b", re.I), "host", 0.70, "похоже на имя хоста"),
    (re.compile(r"\b[a-z][\w.-]*@[\w.-]+\b", re.I), "host", 0.85, "target user@host"),
    (re.compile(r"\b[a-z0-9-]+\.(?:local|lan|internal|example|corp|cloud)(?:[.\w]*)?\b", re.I), "host", 0.80, "домен внутренней сети"),
    (re.compile(r":(\d{2,5})\b"), "port", 0.90, "порт (:NNNN)"),
    (re.compile(r"\b(?:-p|--port|port)\s*[=:]?\s*(\d{2,5})\b", re.I), "port", 0.85, "порт (флаг)"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:/[A-Za-z0-9_./-]+|C:\\[A-Za-z0-9_.\\-]+|%[A-Z0-9_]+%|~[\\/][A-Za-z0-9_.\\/-]+)(?![A-Za-z0-9_-])"), "path", 0.70, "путь"),
    (re.compile(r"\bhttps?://[^\s'\"|;]+", re.I), "url", 0.90, "URL"),
    (re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"), "text", 0.60, "константа в верхнем регистре (возможно env/secret)"),
    (re.compile(r"\b(?:passw(?:ord|d)?|token|secret|apikey|api[_-]?key|client[_-]?secret)\b[=:\s]", re.I), "secret", 0.90, "ключевое слово секрета"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}:\d{2}(?::\d{2})?\b"), "text", 0.60, "дата/время"),
    (re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}"), None, 1.00, "уже размеченный параметр {{...}}"),
]

MARKED_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")


def detect(cmd: str) -> list[dict]:
    hits: list[dict] = []
    for m in MARKED_RE.finditer(cmd):
        hits.append({"token": m.group(1), "type": "marked", "confidence": 1.0,
                     "reason": "явная разметка {{}}", "source": cmd})
    for pattern, ptype, conf, reason in RULES:
        if ptype is None:
            continue
        for m in pattern.finditer(cmd):
            token = m.group(0).lstrip(":")
            # не размечаем уже размеченное и обрезки URL (//server1 из http://)
            if MARKED_RE.search(token):
                continue
            if ptype == "path" and token.startswith("//"):
                continue
            hits.append({"token": token, "type": ptype, "confidence": conf,
                         "reason": reason, "source": cmd})
    return hits


def aggregate(commands: list[str]) -> dict:
    stats: dict[tuple, dict] = {}
    for cmd in commands:
        for h in detect(cmd):
            key = (h["token"], h["type"])
            if key not in stats:
                h2 = {k: v for k, v in h.items() if k != "source"}
                h2["count"] = 0
                h2["commands"] = []
                stats[key] = h2
            stats[key]["count"] += 1
            if cmd not in stats[key]["commands"]:
                stats[key]["commands"].append(cmd)
    items = sorted(stats.values(), key=lambda x: (-x["confidence"], -x["count"]))
    suggested = [i for i in items if i["confidence"] >= 0.60]
    unsure = [i for i in items if 0.40 <= i["confidence"] < 0.60]
    return {"suggested": suggested, "unsure": unsure}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cmd", action="append", default=[])
    ap.add_argument("--file", default="")
    ap.add_argument("--json", action="store_true", help="вывод как JSON")
    args = ap.parse_args()

    commands: list[str] = list(args.cmd)
    if args.file:
        data = read_json(Path(args.file)) if args.file.endswith(".json") else read_yaml(Path(args.file))
        for a in data.get("actions", []):
            if a.get("cmd"):
                commands.append(a["cmd"])
    if not commands:
        print("Нет команд (--cmd или --file).", file=sys.stderr)
        return 2

    result = aggregate(commands)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print("=== Кандидаты в переменные (suggested, conf >= 0.6) ===")
    for i in result["suggested"]:
        print(f'  {i["token"]:<28} {i["type"]:<8} conf={i["confidence"]:.2f} '
              f'раз={i["count"]} — {i["reason"]}')
    print("=== Неуверенные (0.4–0.6): спросить пользователя ===")
    for i in result["unsure"]:
        print(f'  {i["token"]:<28} {i["type"]:<8} conf={i["confidence"]:.2f} — {i["reason"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
