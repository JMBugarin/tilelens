from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
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
    "select_1": "Ctrl+Shift+1",
    "select_2": "Ctrl+Shift+2",
    "select_3": "Ctrl+Shift+3",
}

OVERLAY_MODES = ("exclude", "suppress", "panel_only")
APPEARANCE_MODES = ("auto", "light", "dark")
GUIDE_MODES = ("auto", "always", "off")
HZ_RANGE = (8.0, 12.0)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def clean_offset(value) -> dict | None:
    """{"x", "y"}: where the panel sits from the capture region's top-left corner, in points."""
    if not isinstance(value, dict):
        return None
    x, y = _number(value.get("x")), _number(value.get("y"))
    if x is None or y is None:
        return None
    return {"x": round(x), "y": round(y)}


def clean_region(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    nums = [_number(value.get(k)) for k in ("x", "y", "width", "height")]
    if any(n is None for n in nums) or nums[2] <= 0 or nums[3] <= 0:
        return None
    return dict(zip(("x", "y", "width", "height"), (round(n) for n in nums), strict=True))


def _text_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass
class Settings:
    profile: str | None = None
    words: str | None = None
    region: dict | None = None
    # display that shows the board, for profiles that find the board by color
    board_screen: int | None = None
    calibration: str | None = None
    hz: float = 10.0
    overlay_mode: str = "exclude"
    appearance: str = "auto"
    guides: str = "auto"
    reduce_motion: bool = False
    panel_compact: bool = False
    panel_offset: dict | None = None
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
        if not isinstance(data, dict):
            return cls()
        s = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in known:
                setattr(s, key, value)
        s._validate()
        return s

    def _validate(self) -> None:
        self.profile = _text_or_none(self.profile)
        self.words = _text_or_none(self.words)
        self.calibration = _text_or_none(self.calibration)
        self.region = clean_region(self.region)
        self.panel_offset = clean_offset(self.panel_offset)
        bad_screen = isinstance(self.board_screen, bool) or not isinstance(self.board_screen, int)
        if bad_screen or self.board_screen < 0:
            self.board_screen = None
        merged = dict(DEFAULT_SHORTCUTS)
        given = self.shortcuts if isinstance(self.shortcuts, dict) else {}
        merged.update(
            {k: v for k, v in given.items() if k in DEFAULT_SHORTCUTS and isinstance(v, str)}
        )
        self.shortcuts = merged
        if self.overlay_mode not in OVERLAY_MODES:
            self.overlay_mode = "exclude"
        if self.appearance not in APPEARANCE_MODES:
            self.appearance = "auto"
        if self.guides not in GUIDE_MODES:
            self.guides = "auto"
        if not isinstance(self.reduce_motion, bool):
            self.reduce_motion = False
        if not isinstance(self.panel_compact, bool):
            self.panel_compact = False
        try:
            hz = None if isinstance(self.hz, bool) else float(self.hz)
        except (TypeError, ValueError):
            hz = None
        if hz is not None and not math.isfinite(hz):
            hz = None
        self.hz = min(HZ_RANGE[1], max(HZ_RANGE[0], hz)) if hz is not None else 10.0

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)
