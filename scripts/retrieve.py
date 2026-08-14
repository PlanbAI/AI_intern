"""Ретривал: поиск похожих процедур перед задачей (ОБЯЗАТЕЛЬНЫЙ шаг).

Ранжирование: TF-IDF-термы из index.json + буст за совпадение keywords (0.3)
и тегов (0.2). Уверенность = нормированный на максимум балл (0..1):
  >= 0.40 — «подходящий» кандидат (вариант для выбора/исполнения)
  <  0.40 — слабый (в список выбора не входит; вероятно, новая задача)

Неоднозначность (ambiguous): подходящих кандидатов >= 2 → вывести ВСЕ
варианты, отсортированные по убыванию уверенности, с номерами; выбор по
умолчанию — вариант 1 (самый вероятный).

С host-статистикой (--host): предупреждение, если на этом хосте процедура
уже падала (runs >= 3 и ok/runs < 0.66).

Использование:
  python scripts/retrieve.py --query "запусти приложение на сервере2" --host server2
  python scripts/retrieve.py --query "проверь логи" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import (  # noqa: E402
    INDEX_FILE,
    STATE_DIR,
    now_iso,
    read_json,
    read_yaml,
    tokenize,
)

HIGH = 0.70
MEDIUM = 0.40
# Абсолютный минимум сырого скора для «подходящего» кандидата: защита от
# нормировки, когда единственный кандидат с мизерным совпадением получает 1.00.
SCORE_FLOOR = 0.15
KEYWORD_BOOST = 0.30
TAG_BOOST = 0.20


def _kw_matches(kw: str, qterms: set[str]) -> bool:
    """Точное или префиксное совпадение (русская морфология): «встреч» → «встреча»."""
    for t in qterms:
        if len(kw) < 4 or len(t) < 4:
            continue
        if kw.startswith(t) or t.startswith(kw):
            return True
    return False


def score_procedure(proc: dict, qterms: set[str]) -> tuple[float, set[str], set[str]]:
    score = 0.0
    for t in qterms:
        score += proc.get("terms", {}).get(t, 0.0)
    kw_hits = {k for k in set(proc.get("keywords", [])) if _kw_matches(k, qterms)}
    tag_hits = qterms & set(proc.get("tags", []))
    score += KEYWORD_BOOST * len(kw_hits) + TAG_BOOST * len(tag_hits)
    return score, kw_hits, tag_hits


def host_stats(pid: str, host: str) -> dict | None:
    state_file = STATE_DIR / f"{pid}.yaml"
    if not state_file.exists():
        return None
    state = read_yaml(state_file)
    h = state.get("hosts", {}).get(host)
    if not h:
        return None
    runs, ok = h.get("runs", 0), h.get("ok", 0)
    rate = ok / runs if runs else None
    warn = runs >= 3 and rate is not None and rate < 0.66
    return {"runs": runs, "ok": ok, "ok_rate": round(rate, 3) if rate is not None else None,
            "warn": warn}


def retrieve(query: str, host: str = "", top_k: int = 5, threshold: float = 0.0) -> dict:
    index = read_json(INDEX_FILE)
    qterms = tokenize(query)
    scored: list[tuple[float, dict]] = []
    for proc in index.get("procedures", []):
        score, kw_hits, tag_hits = score_procedure(proc, qterms)
        if score <= 0:
            continue
        scored.append((score, proc, kw_hits, tag_hits))
    scored.sort(key=lambda x: -x[0])
    max_score = scored[0][0] if scored else 1.0

    candidates = []
    for score, proc, kw_hits, tag_hits in scored[:top_k]:
        confidence = round(score / max_score, 3)
        if confidence < threshold:
            continue
        notes = []
        # Статус non-working: только справка — уверенность ×0.5
        if proc.get("status") == "non-working":
            confidence = round(confidence * 0.5, 3)
            notes.append("status: non-working (справка, не для исполнения)")
        # Провал verify НЕ помечает процедуру рабочей: деградация на хосте
        # (runs>=3 и ok/runs<0.66) режет уверенность ×0.75 и снимает high-уровень
        host_data = host_stats(proc["id"], host) if host else None
        degraded = bool(host_data and host_data.get("warn"))
        if degraded:
            confidence = round(confidence * 0.75, 3)
            notes.append(f"⚠️ деградация на хосте {host}: ok {host_data['ok']}/{host_data['runs']}")
        level = ("high" if confidence >= HIGH
                 else "medium" if confidence >= MEDIUM else "low")
        cand = {
            "id": proc["id"],
            "title": proc["title"],
            "status": proc.get("status", "active"),
            "score": round(score, 4),
            "confidence": confidence,
            "level": level,
            "keywords_hit": sorted(kw_hits),
            "tags_hit": sorted(tag_hits),
            "notes": notes,
        }
        if host_data:
            cand["host"] = host_data
        candidates.append(cand)
    # Нумерация только «подходящих»: уверенность >= MEDIUM И сырой скор >= FLOOR.
    for i, cand in enumerate(candidates, 1):
        cand["num"] = i if (cand["confidence"] >= MEDIUM
                            and cand["score"] >= SCORE_FLOOR) else None
    choosable = [c for c in candidates if c["num"]]
    return {"query": query, "host": host, "generated_at": now_iso(),
            "candidates": candidates,
            "ambiguous": len(choosable) >= 2,
            "default_id": choosable[0]["id"] if choosable else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True, help="описание задачи (как скажет пользователь)")
    ap.add_argument("--host", default="", help="целевой хост (для host-статистики)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not INDEX_FILE.exists():
        print("index.json не найден. Сначала: python scripts/index.py", file=sys.stderr)
        return 2
    result = retrieve(args.query, host=args.host, top_k=args.top)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not result["candidates"]:
        print("Похожих процедур не найдено → это новая задача (режим захвата).")
        return 0
    print(f"Кандидаты по запросу: «{args.query}»" + (f" (хост: {args.host})" if args.host else ""))
    weak = 0
    for c in result["candidates"]:
        host_note = ""
        if c.get("host"):
            h = c["host"]
            host_note = f" | host {args.host}: ok {h['ok']}/{h['runs']}"
            if h.get("warn"):
                host_note += " ⚠️ БЫЛИ НЕУДАЧИ"
        notes = (" | " + "; ".join(c["notes"])) if c.get("notes") else ""
        if c.get("num") is None:
            weak += 1
            continue
        default = "  [по умолчанию]" if c["num"] == 1 else ""
        print(f'  {c["num"]}. {c["confidence"]:.2f} [{c["level"]:<6}] {c["id"]} '
              f'{c["title"]} (статус: {c["status"]}){default}{host_note}{notes}')
        if c["keywords_hit"]:
            print(f'       keywords: {", ".join(c["keywords_hit"])}')
    if weak:
        print(f"  …и {weak} слабых совпадений ниже {MEDIUM:.2f} (не входят в выбор).")
    if result["ambiguous"]:
        print(f"\nНеоднозначность: подходит несколько вариантов. "
              f"Выберите номер (Enter = вариант 1: {result['default_id']}).")
    elif result["default_id"]:
        print(f"\nПодходящий вариант один: {result['default_id']}. "
              f"Подтвердите выполнение (или Enter).")
    else:
        print("\nПодходящих вариантов (>= 0.40) нет → это новая задача (режим захвата).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())