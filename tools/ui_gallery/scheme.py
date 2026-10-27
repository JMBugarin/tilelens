"""What a run forces on the application: light or dark, the widget style, motion and native blur.

The offscreen platform ignores QStyleHints.setColorScheme, so the answer of colorScheme() is
replaced as well, the application palette is set, and colorSchemeChanged is emitted.

When tilelens.ui.design is present and the application installs its style (the gallery looks for
apply_app_style outside the design package), the run mirrors that: the same style, palette and
stylesheet per material, the same default material, and glass windows forced to the material of
the scheme. Motion is switched off so every state is its final state, and the native blur that a
window asks its platform for is answered with a fake yes (or a no) so that the window paints the
body it would paint on a real screen. begin() saves everything it touches; end() puts it back,
which matters when the gallery runs inside another test process.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QStyleHints
from PySide6.QtWidgets import QApplication, QStyleFactory

_ORIGINAL = QStyleHints.colorScheme

SCHEMES = ("default", "light", "dark")
STYLE_MODES = ("auto", "design", "plain")
BLUR_MODES = ("auto", "on", "off")

_run: dict = {}
_uses_cache: list[bool] = []


def design():
    """tilelens.ui.design, or None when it is missing or does not import."""
    try:
        return importlib.import_module("tilelens.ui.design")
    except ImportError:
        return None
    except Exception as exc:
        print(f"ui_gallery: tilelens.ui.design cannot be imported: {exc!r}", file=sys.stderr)
        return None


def app_uses_design() -> bool:
    """True when application code outside the design package installs the design's style."""
    if not _uses_cache:
        found = False
        root = Path(importlib.import_module("tilelens").__file__).resolve().parent
        for path in root.rglob("*.py"):
            if "design" in path.relative_to(root).parts:
                continue
            try:
                found = "apply_app_style" in path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if found:
                break
        _uses_cache.append(found)
    return _uses_cache[0]


def _palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        window, base, text = QColor(30, 30, 30), QColor(44, 44, 46), QColor(240, 240, 245)
        button, disabled = QColor(58, 58, 60), QColor(130, 130, 136)
    else:
        window, base, text = QColor(236, 236, 238), QColor(255, 255, 255), QColor(20, 20, 24)
        button, disabled = QColor(246, 246, 248), QColor(140, 140, 146)
    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: window,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: button,
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: base,
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: disabled,
        QPalette.ColorRole.Highlight: QColor(10, 132, 255),
        QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
    }
    for role, color in roles.items():
        p.setColor(role, color)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


def set_style(app: QApplication, name: str) -> bool:
    """fusion, native or any QStyleFactory key. False (with a note) when it is not available."""
    available = QStyleFactory.keys()
    keys = {k.lower(): k for k in available}
    if name == "native":
        wanted = {"darwin": ["macos"], "win32": ["windows11", "windowsvista", "windows"]}
        name = next((k for k in wanted.get(sys.platform, []) if k in keys), "fusion")
    key = keys.get(name.lower())
    if key is None:
        print(f"ui_gallery: no style {name!r} (have {sorted(keys)}); using fusion", file=sys.stderr)
        key = keys.get("fusion", "Fusion")
    style = QStyleFactory.create(key)
    if style is None:
        return False
    app.setStyle(style)
    return True


def _fake_blur(win_id: int, radius_px: float, dark: bool) -> bool:
    return True


def begin(
    app: QApplication, *, style: str | None, app_style: str, blur: str, animate: bool
) -> dict:
    """Prepare the application for a run; returns what was decided, for the log."""
    d = design()
    if app_style == "design" and d is None:
        raise SystemExit("--app-style design needs tilelens.ui.design, which cannot be imported")
    uses = d is not None and (app_style == "design" or (app_style == "auto" and app_uses_design()))
    use_blur = blur == "on" or (blur == "auto" and uses)
    _run.clear()
    _run.update(
        design=d if uses else None,
        explicit_style=style,
        saved=dict(
            style=app.style().name(),
            palette=QPalette(app.palette()),
            font=QFont(app.font()),
            sheet=app.styleSheet(),
        ),
    )
    if d is not None:
        _run["saved"].update(
            reduce=d.reduce_motion(),
            material=d.default_material(),
            backdrop=vars(d.GlassWindow).get("backdrop"),
        )
        d.set_reduce_motion(not animate)
        d.GlassWindow.backdrop = staticmethod(_fake_blur) if use_blur else None
    if style is not None or not uses:
        set_style(app, style or "fusion")
    elif d is not None:
        app.setStyle(d.GlassStyle())
    return {
        "design": uses,
        "blur": use_blur,
        "motion": bool(animate),
        "reduce_motion": d is not None and not animate,
    }


def current() -> str:
    return _run.get("scheme", "default")


def apply(name: str | None) -> None:
    """Switch to the light or dark appearance, or back to what the platform reports."""
    app = QGuiApplication.instance()
    if app is None:
        return
    hints = app.styleHints()
    default = name in (None, "default")
    if _run.get("saved") is not None:
        _run["scheme"] = "default" if default else name
    if default:
        QStyleHints.colorScheme = _ORIGINAL
        hints.setColorScheme(Qt.ColorScheme.Unknown)
    else:
        scheme = Qt.ColorScheme.Dark if name == "dark" else Qt.ColorScheme.Light
        QStyleHints.colorScheme = lambda self, s=scheme: s
        hints.setColorScheme(scheme)
    _restyle(app, name, default)
    if not default:
        hints.colorSchemeChanged.emit(scheme)
        app.processEvents()


def _restyle(app, name: str | None, default: bool) -> None:
    saved = _run.get("saved")
    if saved is None:
        return
    d = _run.get("design")
    if d is not None:
        material = d.system_material() if default else d.MATERIALS[name]
        d.set_default_material(material)
        d.apply_app_style(app, material, widget_style=False)
    elif default:
        app.setPalette(saved["palette"])
    else:
        app.setPalette(_palette(name == "dark"))


def force_windows(windows, name: str | None = None) -> None:
    """Give glass windows the material of the scheme; they would follow the backdrop otherwise.
    Without a name it is the scheme of the run in progress."""
    d = _run.get("design")
    name = name or current()
    if d is None or name == "default":
        return
    material = d.MATERIALS[name]
    for w in windows:
        setter = getattr(w, "set_material", None)
        if callable(setter) and hasattr(w, "glass_material"):
            setter(material)


def end() -> None:
    """Undo begin() and apply()."""
    app = QGuiApplication.instance()
    QStyleHints.colorScheme = _ORIGINAL
    saved = _run.pop("saved", None)
    d = design()
    _run.clear()
    if app is None or saved is None:
        return
    app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
    if d is not None:
        d.set_reduce_motion(saved["reduce"])
        d.set_default_material(saved["material"])
        d.GlassWindow.backdrop = saved["backdrop"]
    app.setStyleSheet(saved["sheet"])
    app.setPalette(saved["palette"])
    app.setFont(saved["font"])
    QApplication.setStyle(saved["style"])
