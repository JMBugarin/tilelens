from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("TILELENS_HOME")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tilelens"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "tilelens"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tilelens"


def words_dir() -> Path:
    return data_dir() / "words"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def calibration_dir() -> Path:
    return data_dir() / "calibrations"


def debug_dump_dir() -> Path:
    return data_dir() / "debug_dumps"
