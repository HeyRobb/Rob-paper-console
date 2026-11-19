"""Load PC1_* settings from flat KEY=VALUE files in the repository root.

Two files are read, in this order:

* ``printer.env``        - tracked in git, shared defaults for supported hardware
* ``printer.local.env``  - optional, git-ignored, per-device overrides

Values already present in the real process environment (for example from a
systemd ``Environment=`` line) always win, and values from ``printer.local.env``
win over ``printer.env``. Only keys starting with ``PC1_`` are accepted.

This must run before any module that reads its settings at import time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE_NAMES = ("printer.local.env", "printer.env")  # highest priority first
DISABLE_VAR = "PC1_DISABLE_ENV_FILE"

_KEY_RE = re.compile(r"^PC1_[A-Z0-9_]+$")


def parse_env_text(text: str) -> Dict[str, str]:
    """Parse KEY=VALUE lines. Ignores blanks, comments and non-PC1_ keys."""
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Allow trailing "  # comment" on unquoted values.
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def load_env_files(
    directory: Optional[Path] = None,
    names: Iterable[str] = ENV_FILE_NAMES,
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Apply settings files to ``environ`` (default ``os.environ``).

    Returns the settings that were actually applied. Existing environment
    values are never overwritten.
    """
    target = os.environ if environ is None else environ
    if target.get(DISABLE_VAR, "").strip().lower() in ("1", "true", "yes", "on"):
        return {}

    base = REPO_ROOT if directory is None else Path(directory)
    applied: Dict[str, str] = {}
    for name in names:  # first file listed wins over later ones
        path = base / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for key, value in parse_env_text(text).items():
            if key not in target:
                target[key] = value
                applied[key] = value
    return applied
