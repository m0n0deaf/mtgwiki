from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def write_json(path: str | Path, data: Any, *, indent: int = 2) -> Path:
    """Write JSON with UTF-8 and return the resulting path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent, default=str) + "\n",
        encoding="utf-8",
    )
    return target


def write_jsonl(path: str | Path, records: Iterable[Any]) -> Path:
    """Write one JSON object/value per line and return the resulting path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str))
            handle.write("\n")
    return target


def read_jsonl(path: str | Path) -> list[Any]:
    """Read a JSON Lines file created by :func:`write_jsonl`."""
    result: list[Any] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                result.append(json.loads(line))
    return result
