"""Сохранение процедуры в память: валидация + атомарная запись + инструкция.

Шаги (все под lockfile):
  1. валидация procedures-файла по schema/procedure.schema.json;
  2. запись procedures/<id>.yaml (отказ при существующем id, кроме --force);
  3. инициализация state/<id>.yaml с биндингами (--bindings "HOST=server1;PORT=8080");
  4. рендер человекочитаемой instructions/<id>.md.

После сохранения обязательно: python scripts/index.py и git-коммит.

Использование:
  python scripts/save-procedure.py --input draft_final.yaml --host server1 \
      --bindings "HOST=server1;PORT=8080;APP_DIR=/opt/app"
  python scripts/save-procedure.py --input draft_final.yaml --host server1 --id P007 --force
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import yaml  # noqa: E402
from common import (  # noqa: E402
    INSTRUCTIONS_DIR,
    MEMORY_DIR,
    PROCEDURES_DIR,
    SCHEMA_VERSION,
    STATE_DIR,
    MemoryLock,
    atomic_write_json,
    atomic_write_text,
    now_iso,
    read_json,
    read_yaml,
)
from jsonschema import Draft7Validator  # noqa: E402


def load_schema() -> dict:
    schema = read_json(MEMORY_DIR.parent / "schema" / "procedure.schema.json")
    return schema


def validate(proc: dict) -> list[str]:
    errors: list[str] = []
    try:
        Draft7Validator(load_schema()).validate(proc)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    return errors


def parse_bindings(raw: str) -> dict:
    bindings: dict = {}
    if not raw:
        return bindings
    for pair in raw.split(";"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        bindings[k.strip()] = v.strip()
    return bindings


def render_instruction(proc: dict) -> str:
    """Человекочитаемая инструкция — чтобы человек мог выполнить всё сам."""
    lines: list[str] = []
    lines.append(f"# {proc.get('title', 'Без названия')}")
    badge = "НЕРАБОЧЕЕ РЕШЕНИЕ" if proc.get("status") == "non-working" else "рабочая процедура"
    lines.append(f"\n> Статус: **{badge}** · id: `{proc['id']}` · версия схемы: {proc.get('schema_version')}")
    if proc.get("description"):
        lines.append(f"\n{proc['description']}")
    if proc.get("deprecated_note"):
        lines.append(f"\n> ⚠️ НЕРАБОЧЕЕ РЕШЕНИЕ ({proc.get('updated_at', '')}): {proc['deprecated_note']}")

    params = proc.get("parameters", [])
    if params:
        lines.append("\n## Параметры\n")
        lines.append("| Параметр | Тип | Пример | Пояснение |")
        lines.append("|---|---|---|---|")
        for p in params:
            var = "переменная" if p.get("is_variable", True) else "константа"
            lines.append(f"| `{p['name']}` | {p['type']} ({var}) | `{p.get('example', '')}` | {p.get('note', '')} |")

    deps = proc.get("dependencies", [])
    lines.append("\n## Предпосылки")
    lines.append("\n- " + ("; ".join(deps)) if deps else "\n- Нет (процедура автономна)")

    lines.append("\n## Шаги\n")
    for step in proc.get("steps", []):
        lines.append(f"### {step['order']}. {step['description']}\n")
        lines.append("```\n" + step.get("command", "") + "\n```")
        if step.get("check"):
            c = step["check"]
            lines.append(f"\n_Проверка: {c['type']} → {c['value']}_")
        conf = "🔒 подтверждение пользователя" if step.get("requires_confirmation") else ""
        if conf:
            lines.append(f"\n_{conf}_")
        lines.append("")

    criteria = proc.get("criteria", [])
    lines.append("\n## Критерии успеха\n")
    for c in criteria:
        lines.append(f"- [{c['type']}] {c['value']}")
    if not criteria:
        lines.append("- (критерии не заданы — согласуй с владельцем процедуры)")

    lines.append("\n## Если что-то пошло не так\n")
    lines.append("- Проверь критерии успеха выше: какой именно шаг не прошёл проверку?")
    lines.append("- Сверь параметры (хост, порты, пути) с таблицей в начале инструкции.")
    lines.append("- Лимиты: таймаут шага — "
                 f"{proc.get('timeout_seconds', 600)} с, максимум шагов — {proc.get('max_steps', 20)}.")
    lines.append("- Найди похожие инструкции в этой же папке и раздел «НЕРАБОЧЕЕ РЕШЕНИЕ»: "
                 "возможно, процедура уже модифицировалась.")
    lines.append("\n---")
    lines.append(f"_Сгенерировано автоматически {now_iso()} · источник: procedures/{proc['id']}.yaml_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="финальный YAML процедуры")
    ap.add_argument("--host", required=True, help="хост, на котором наблюдалась процедура")
    ap.add_argument("--bindings", default="", help='биндинги: "HOST=server1;PORT=8080"')
    ap.add_argument("--id", default="", help="принудительный id (по умолчанию — из файла)")
    ap.add_argument("--force", action="store_true", help="перезаписать существующую процедуру")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"Файл не найден: {src}", file=sys.stderr)
        return 2
    proc = read_yaml(src)
    proc.setdefault("schema_version", SCHEMA_VERSION)
    if args.id:
        proc["id"] = args.id

    errors = validate(proc)
    if errors:
        print("Ошибки валидации (schema/procedure.schema.json):", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        return 1

    proc.setdefault("created_at", now_iso())
    proc["updated_at"] = now_iso()
    pid = proc["id"]
    proc_file = PROCEDURES_DIR / f"{pid}.yaml"
    state_file = STATE_DIR / f"{pid}.yaml"

    bindings = parse_bindings(args.bindings)
    state = {
        "schema_version": SCHEMA_VERSION,
        "procedure_id": pid,
        "hosts": {
            args.host: {
                "bindings": bindings,
                "runs": 0,
                "ok": 0,
                "last_result": "unknown",
                "last_error": "",
                "last_run_at": "",
            }
        },
    }

    with MemoryLock(timeout_sec=15):
        if proc_file.exists() and not args.force:
            print(f"Процедура {pid} уже существует (нужен --force для перезаписи).", file=sys.stderr)
            return 1
        atomic_write_text(proc_file, yaml.safe_dump(proc, allow_unicode=True, sort_keys=False))
        atomic_write_json(state_file, state)
        INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(INSTRUCTIONS_DIR / f"{pid}.md", render_instruction(proc))

    print(f"Сохранено: {proc_file.name}")
    print(f"  state:  {state_file.name} (host={args.host}, bindings={bindings})")
    print(f"  инструкция: instructions/{pid}.md")
    print("Далее: python scripts/index.py и git-коммит.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
