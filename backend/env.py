"""Minimal ``.env`` reader for local secrets such as API keys.

The standard library only, so no dependency is needed for an optional
integration. The real process environment always wins over the file, and
nothing here interpolates or executes: a line is ``KEY=value`` or it is ignored.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_KEY = re.compile(r"^[A-Za-z_]\w*$")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # `auto   # auto | local` style comments
        if value:
            values[key] = value
    return values


def load_env_file(path: Path) -> list[str]:
    """Set variables from ``path`` that are not already in the environment; return the names set."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded = []
    for key, value in parse_env(text).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
