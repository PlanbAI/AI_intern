"""Общие утилиты агента-стажёра: пути, блокировка, атомарная запись."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = PROJECT_ROOT / "agent-memory"
PROCEDURES_DIR = MEMORY_DIR / "procedures"
STATE_DIR = MEMORY_DIR / "state"
INSTRUCTIONS_DIR = MEMORY_DIR / "instructions"
TELEMETRY_DIR = MEMORY_DIR / "telemetry"
INDEX_FILE = MEMORY_DIR / "index.json"
LOCK_FILE = MEMORY_DIR / ".lock"
SCHEMA_VERSION = 1


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class LockError(TimeoutError):
    pass


class MemoryLock:
    """Файловая блокировка (создание эксклюзивного файла) с таймаутом."""

    def __init__(self, timeout_sec: float = 10.0):
        self.timeout_sec = timeout_sec
        self.acquired = False

    def __enter__(self) -> "MemoryLock":
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "ts": now_iso()}).encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise LockError(
                        f"Блокировка {LOCK_FILE} не получена за {self.timeout_sec}с. "
                        "Удалите файл, если другой процесс неактивен."
                    )
                time.sleep(0.2)

    def __exit__(self, *exc) -> None:
        if self.acquired:
            try:
                LOCK_FILE.unlink()
            except FileNotFoundError:
                pass
            self.acquired = False


def atomic_write_text(path: Path, content: str) -> None:
    """Атомарная запись: temp-файл + rename (нет частично записанных файлов)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def iter_procedures():
    """Процедуры из procedures/*.yaml, пропуская служебные (с ведущим _)."""
    for path in sorted(PROCEDURES_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        yield path
