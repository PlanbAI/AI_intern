"""Пересборка index.json из procedures/. Единственный писатель индекса.

Индекс — производный артефакт: НЕ редактируй руками, гоняй этот скрипт.
Термы: TF-IDF (частотный вес слова из title+description+keywords+tags,
домноженный на IDF по корпусу) — используется retrieve.py.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent / "lib"))

from common import (  # noqa: E402
    INDEX_FILE,
    MemoryLock,
    atomic_write_json,
    iter_procedures,
    now_iso,
    read_yaml,
)

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def terms_of(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text or "")]


def build_index() -> dict:
    entries = []
    for path in iter_procedures():
        try:
            proc = read_yaml(path)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! пропуск {path.name}: не удалось прочитать YAML: {exc}")
            continue
        if not proc.get("id") or not proc.get("title"):
            print(f"  ! пропуск {path.name}: нет id/title")
            continue
        text = " ".join(
            [
                proc.get("title", ""),
                proc.get("description", ""),
                " ".join(proc.get("keywords", [])),
                " ".join(proc.get("tags", [])),
            ]
        )
        freqs = Counter(terms_of(text))
        total = sum(freqs.values()) or 1
        entries.append(
            {
                "id": proc["id"],
                "file": path.name,
                "title": proc.get("title", ""),
                "tags": [t.lower() for t in proc.get("tags", [])],
                "keywords": [k.lower() for k in proc.get("keywords", [])],
                "status": proc.get("status", "active"),
                "terms": {t: round(c / total, 5) for t, c in freqs.items()},
            }
        )
    n = len(entries)
    # IDF по корпусу: idf = ln(1 + N / (1 + df))
    df: Counter = Counter()
    for e in entries:
        df.update(e["terms"].keys())
    for e in entries:
        e["terms"] = {
            t: round(w * math.log(1 + n / (1 + df[t])), 6)
            for t, w in e["terms"].items()
            if t in df
        }
    return {
        "version": 2,
        "generated_at": now_iso(),
        "procedure_count": n,
        "procedures": entries,
    }


def main() -> int:
    with MemoryLock(timeout_sec=15):
        index = build_index()
        atomic_write_json(INDEX_FILE, index)
    print(f"index.json пересобран: процедур {index['procedure_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
