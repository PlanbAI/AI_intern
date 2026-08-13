"""Черновик процедуры из наблюдаемых действий пользователя (режим «живой»).

Вход — файл сессии (JSON или YAML):
{
  "session": "s-2026-08-13-01",
  "title": "Запуск приложения на сервере",
  "description": "...",
  "host": "server1",
  "actions": [
    {"cmd": "scp build.tar.gz deploy@server1:/opt/app/", "desc": "Копируем артефакт"},
    {"cmd": "...", "desc": "..."}
  ]
}

Выход: черновик процедуры (без {{}}-разметки — её делает LLM в скилле
с батч-подтверждением) + кандидаты переменных от param-detect.py.
Скрипт НЕ пишет в память — только создаёт черновик.

Использование:
  python scripts/capture.py --input sessions/s01.json
  python scripts/capture.py --input sessions/s01.json --out agent-memory/drafts/P001.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import PROCEDURES_DIR, read_json, read_yaml  # noqa: E402
from param_detect import aggregate  # noqa: E402


def next_id() -> str:
    ids: list[int] = []
    for path in PROCEDURES_DIR.glob("P*.yaml"):
        try:
            ids.append(int(path.stem[1:]))
        except ValueError:
            continue
    return f"P{max(ids, default=0) + 1:03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="файл сессии JSON/YAML")
    ap.add_argument("--out", default="", help="путь для черновика YAML (по умолчанию печать в stdout)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 2
    data = read_json(path) if path.suffix.lower() == ".json" else read_yaml(path)

    actions = data.get("actions", [])
    if not actions:
        print("В файле сессии нет действий (actions[]).", file=sys.stderr)
        return 2

    commands = [a.get("cmd", "") for a in actions]
    candidates = aggregate(commands)

    steps = []
    seen: set[str] = set()
    for i, a in enumerate(actions, 1):
        cmd = a.get("cmd", "").strip()
        desc = a.get("desc", "").strip() or cmd
        if cmd in seen:  # повтор в рамках одной сессии — не дублируем шаг
            continue
        seen.add(cmd)
        steps.append({"order": len(steps) + 1, "description": desc, "command": cmd})

    draft = {
        "schema_version": 1,
        "id": data.get("id") or next_id(),
        "title": data.get("title") or "Без названия",
        "description": data.get("description", ""),
        "tags": data.get("tags", []),
        "keywords": data.get("keywords", []),
        "owner": data.get("owner", ""),
        "status": "active",
        "parameters": [],  # заполняет LLM: имя, тип, is_variable, example
        "steps": steps,
        "dependencies": [],
        "criteria": [],    # заполняет агент + согласует пользователь
        "timeout_seconds": data.get("timeout_seconds", 600),
        "max_steps": max(len(steps) * 2, 20),
        "_draft_meta": {
            "session": data.get("session", ""),
            "observed_host": data.get("host", ""),
            "parameter_candidates": candidates,
        },
    }

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        import yaml as ymod

        out.write_text(
            ymod.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        print(f"Черновик: {out}")
        print(f"Кандидатов переменных: suggested={len(candidates['suggested'])}, "
              f"unsure={len(candidates['unsure'])}")
    else:
        if args.json:
            print(json.dumps(draft, ensure_ascii=False, indent=2))
        else:
            print(f"id: {draft['id']} | title: {draft['title']} | шагов: {len(steps)}")
            for s in steps:
                print(f"  {s['order']}. [{s['command']}]  {s['description']}")
            print("\nКандидаты переменных (LLM: объедини с few-shot-разметкой):")
            for i in candidates["suggested"]:
                print(f'  {i["token"]:<28} {i["type"]:<8} conf={i["confidence"]:.2f} x{i["count"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
