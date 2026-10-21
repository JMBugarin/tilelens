from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tilelens.paths import settings_path
from tilelens.platforms.base import Region

DEFAULT_SHORTCUTS = {
    "pause": "Ctrl+Shift+P",
    "rescan": "Ctrl+Shift+R",
    "hide_preview": "Ctrl+Shift+H",
    "next_card": "Ctrl+Shift+N",
    "check_tiles": "Ctrl+Shift+C",
    "recalibrate": "Ctrl+Shift+K",
}

OVERLAY_MODES = ("exclude", "suppress", "panel_only")


@dataclass
class Settings:
    profile: str | None = None
    words: str | None = None
    region: dict | None = None
    calibration: str | None = None
    hz: float = 10.0
    overlay_mode: str = "exclude"
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))

    @property
    def region_obj(self) -> Region | None:
        return Region.from_dict(self.region) if self.region else None

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or settings_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        s = cls()
        for key, value in data.items():
            if hasattr(s, key):
                setattr(s, key, value)
        merged = dict(DEFAULT_SHORTCUTS)
        merged.update({k: v for k, v in (s.shortcuts or {}).items() if k in DEFAULT_SHORTCUTS})
        s.shortcuts = merged
        if s.overlay_mode not in OVERLAY_MODES:
            s.overlay_mode = "exclude"
        s.hz = min(12.0, max(8.0, float(s.hz)))
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)
