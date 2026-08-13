"""Ретривал: поиск похожих процедур перед задачей (ОБЯЗАТЕЛЬНЫЙ шаг).

Ранжирование: TF-IDF-термы из index.json + буст за совпадение keywords (0.3)
и тегов (0.2). Уверенность = нормированный на максимум балл (0..1):
  >= 0.70 — высокая (кандидат для P0, всё равно показать пользователю)
  0.40–0.70 — средняя (показать кандидатов, требуется подтверждение)
  <  0.40 — низкая (вероятно, новая задача — режим захвата)

С host-статистикой (--host): предупреждение, если на этом хосте процедура
уже падала (runs >= 3 и ok/runs < 0.66).

Использование:
  python scripts/retrieve.py --query "запусти приложение на сервере2" --host server2
  python scripts/retrieve.py --query "проверь логи" --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import INDEX_FILE, STATE_DIR, now_iso, read_json, read_yaml  # noqa: E402

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
HIGH = 0.70
MEDIUM = 0.40
KEYWORD_BOOST = 0.30
TAG_BOOST = 0.20


def tokenize(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text or "")}


def score_procedure(proc: dict, qterms: set[str]) -> tuple[float, set[str], set[str]]:
    score = 0.0
    for t in qterms:
        score += proc.get("terms", {}).get(t, 0.0)
    kw_hits = qterms & set(proc.get("keywords", []))
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
    return {"query": query, "host": host, "generated_at": now_iso(),
            "candidates": candidates}


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
    for c in result["candidates"]:
        host_note = ""
        if c.get("host"):
            h = c["host"]
            host_note = f" | host {args.host}: ok {h['ok']}/{h['runs']}"
            if h.get("warn"):
                host_note += " ⚠️ БЫЛИ НЕУДАЧИ"
        notes = (" | " + "; ".join(c["notes"])) if c.get("notes") else ""
        print(f'  {c["confidence"]:.2f} [{c["level"]:<6}] {c["id"]} '
              f'{c["title"]} (статус: {c["status"]}){host_note}{notes}')
        if c["keywords_hit"]:
            print(f'       keywords: {", ".join(c["keywords_hit"])}')
    print("\nПороги: >=0.70 высокая (P0 допустим, но показать), 0.40–0.70 средняя, "
          "<0.40 новая задача.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())