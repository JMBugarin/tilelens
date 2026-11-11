"""Stylesheet and palette for the standard widgets that dialogs and settings use.

Boxes, borders and colours come from QSS; indicators that QSS can only draw from image files
(check marks, combo and spin arrows) are painted as vectors by GlassStyle instead. Text colour
of plain labels comes from the palette, so a GlassWindow that sets both repaints them with it.
"""

from __future__ import annotations

import os
import tempfile
import zlib
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QProxyStyle, QStyle

from tilelens.ui.design.appearance import material_of
from tilelens.ui.design.icons import paint_icon
from tilelens.ui.design.material import paint_wash
from tilelens.ui.design.paint import dim, paint_control_rim
from tilelens.ui.design.tokens import Material, mix, rgba, type_font, with_alpha

_WHITE = rgba(255, 255, 255)
_BLACK = rgba(0, 0, 0)


_glyph_cache: dict[tuple, str] = {}


def glyph_dir() -> Path:
    return Path(tempfile.gettempdir()) / "tilelens-ui-glyphs"


def glyph_url(name: str, color: QColor, size: int) -> str | None:
    """Path of a small PNG of an icon (plus its @2x), for the QSS arrows that need an image.

    Written once per colour and size. None when the files cannot be written: the arrows are
    then simply left to the widget style.
    """
    key = (name, color.rgba(), size)
    if key in _glyph_cache:
        return _glyph_cache[key]
    url = None
    try:
        folder = glyph_dir()
        folder.mkdir(parents=True, exist_ok=True)
        images = [_render_glyph(name, color, size, scale) for scale in (1, 2)]
        # named by content, so an icon redrawn later is never served from an older file
        digest = zlib.crc32(bytes(images[0].constBits()))
        stem = f"{name}-{color.rgba():08x}-{size}-{digest:08x}"
        for img, suffix in zip(images, ("", "@2x"), strict=True):
            _write_png(img, folder / f"{stem}{suffix}.png")
        url = (folder / f"{stem}.png").as_posix()
    except OSError:
        url = None
    _glyph_cache[key] = url
    return url


def _render_glyph(name: str, color: QColor, size: int, scale: int) -> QImage:
    side = size * scale
    img = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_icon(p, name, QRectF(0, 0, side, side), color, 2.0)
    p.end()
    return img


def _write_png(img: QImage, path: Path) -> None:
    """Save through a private name and rename, so a reader never sees half a file."""
    if path.exists():
        return
    part = path.with_name(f"{path.stem}.{os.getpid()}.part")
    if not img.save(str(part), "PNG"):
        raise OSError(f"cannot write {part}")
    try:
        os.replace(part, path)
    except OSError:
        part.unlink(missing_ok=True)
        if not path.exists():
            raise


def css(c: QColor) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def _flatten(c: QColor, base: QColor) -> QColor:
    a = c.alphaF()
    return QColor(
        round(c.red() * a + base.red() * (1 - a)),
        round(c.green() * a + base.green() * (1 - a)),
        round(c.blue() * a + base.blue() * (1 - a)),
    )


def surface(m: Material, lift: float = 0.0) -> QColor:
    """An opaque stand-in for the glass, for windows and popups that cannot blur."""
    base = rgba(14, 16, 26) if m.dark else rgba(242, 244, 250)
    mid = mix(m.solid_top, m.solid_bottom, 0.5)
    flat = _flatten(mid, base)
    return mix(flat, _WHITE, lift)


def palette_for(m: Material) -> QPalette:
    pal = QPalette()
    window = surface(m)
    base = _flatten(m.fill_hover, window)
    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: m.text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: _flatten(m.fill, window),
        QPalette.ColorRole.Text: m.text,
        QPalette.ColorRole.PlaceholderText: m.text3,
        QPalette.ColorRole.Button: _flatten(m.fill_hover, window),
        QPalette.ColorRole.ButtonText: m.text,
        QPalette.ColorRole.BrightText: m.text,
        QPalette.ColorRole.Highlight: m.accent,
        QPalette.ColorRole.HighlightedText: m.on_accent,
        QPalette.ColorRole.ToolTipBase: surface(m, 0.06),
        QPalette.ColorRole.ToolTipText: m.text,
        QPalette.ColorRole.Link: m.accent,
        QPalette.ColorRole.Mid: m.divider,
        QPalette.ColorRole.Dark: m.divider,
        QPalette.ColorRole.Shadow: m.shadow,
    }
    for role, color in roles.items():
        pal.setColor(role, color)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, m.text3)
    return pal


def _arrow_rules(m: Material) -> str:
    down = glyph_url("chevron-down", m.text2, 12)
    up = glyph_url("chevron-up", m.text2, 12)
    if down is None or up is None:
        return ""
    return f"""
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 26px;
    border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 22px;
    border: none; background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
    border: none; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{down}"); width: 12px; height: 12px;
}}
"""


def build_qss(m: Material) -> str:
    window = css(surface(m))
    popup = css(surface(m, 0.05))
    accent = css(m.accent)
    accent_hover = css(mix(m.accent, _WHITE, 0.14))
    accent_press = css(mix(m.accent, _BLACK, 0.14))
    selection = css(with_alpha(m.accent, 0.35 if m.dark else 0.25))
    scroll = css(with_alpha(m.text, 0.22 if m.dark else 0.20))
    scroll_hover = css(with_alpha(m.text, 0.36 if m.dark else 0.34))
    handle_edge = css(rgba(0, 0, 0, 0.18 if m.dark else 0.16))
    arrows = _arrow_rules(m)
    return f"""
QDialog {{ background: {window}; }}
QLabel {{ background: transparent; color: {css(m.text)}; }}
QLabel:disabled {{ color: {css(m.text3)}; }}

QToolTip {{
    background: {popup}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
    border-radius: 8px; padding: 4px 8px;
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    min-height: 26px; padding: 0 10px; border-radius: 10px;
    background: {css(m.fill)}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
    selection-background-color: {selection}; selection-color: {css(m.text)};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    background: {css(m.fill_hover)};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QComboBox:on {{
    border: 1px solid {accent}; background: {css(m.fill_hover)};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {css(m.text3)}; background: {css(m.fill)}; border: 1px solid {css(m.divider)};
}}

QComboBox {{ padding-right: 26px; }}
{arrows}
QComboBox QAbstractItemView {{
    background: {popup}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
    border-radius: 10px; padding: 4px; outline: 0;
    selection-background-color: {css(m.fill_press)}; selection-color: {css(m.text)};
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected {{
    background: {css(m.fill_press)}; color: {css(m.text)}; border: none;
}}

QTextEdit, QPlainTextEdit {{
    padding: 6px 8px; border-radius: 10px;
    background: {css(m.fill)}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
    selection-background-color: {selection}; selection-color: {css(m.text)};
}}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {accent}; }}

QSpinBox, QDoubleSpinBox {{ padding: 0 22px 0 0; }}
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit, QComboBox QLineEdit {{
    min-height: 0; padding: 0; margin: 0; border: none; border-radius: 0;
    background: transparent;
}}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; color: {css(m.text)}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {css(m.text3)}; }}

QPushButton {{
    min-height: 28px; padding: 0 16px; border-radius: 14px;
    background: {css(m.fill)}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
}}
QPushButton:hover {{ background: {css(m.fill_hover)}; }}
QPushButton:pressed {{ background: {css(m.fill_press)}; }}
QPushButton:focus {{ border: 1px solid {accent}; }}
QPushButton:default {{
    background: {accent}; color: {css(m.on_accent)}; border: 1px solid {accent};
}}
QPushButton:default:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton:default:pressed {{ background: {accent_press}; border-color: {accent_press}; }}
QPushButton:disabled {{
    color: {css(m.text3)}; background: {css(m.fill)}; border: 1px solid {css(m.divider)};
}}

QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {css(m.fill_press)}; }}
QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {accent}; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 9px;
    background: white; border: 1px solid {handle_edge};
}}
QSlider::groove:vertical {{ width: 4px; border-radius: 2px; background: {css(m.fill_press)}; }}
QSlider::add-page:vertical {{ width: 4px; border-radius: 2px; background: {accent}; }}
QSlider::handle:vertical {{
    width: 16px; height: 16px; margin: 0 -6px; border-radius: 9px;
    background: white; border: 1px solid {handle_edge};
}}
QSlider::handle:disabled {{ background: {css(m.text3)}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {scroll}; border-radius: 3px; min-width: 28px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {scroll_hover};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {popup}; color: {css(m.text)}; border: 1px solid {css(m.divider)};
    border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; margin: 1px 0; }}
QMenu::item:selected {{ background: {accent}; color: {css(m.on_accent)}; }}
QMenu::item:disabled {{ color: {css(m.text3)}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {css(m.divider)}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}
"""


class GlassStyle(QProxyStyle):
    """Fusion with vector indicators in the colours of the widget's material."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
            QStyle.PixelMetric.PM_ExclusiveIndicatorWidth,
            QStyle.PixelMetric.PM_ExclusiveIndicatorHeight,
        ):
            return 18
        return super().pixelMetric(metric, option, widget)

    def polish(self, widget) -> None:
        super().polish(widget)
        # QSS padding does not reach the editor embedded in a spin box or combo box
        if isinstance(widget, QAbstractSpinBox | QComboBox):
            edit = widget.lineEdit()
            if edit is not None:
                edit.setTextMargins(10, 0, 0, 0)

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        pe = QStyle.PrimitiveElement
        if element in (pe.PE_IndicatorCheckBox, pe.PE_IndicatorRadioButton):
            self._draw_choice(element == pe.PE_IndicatorRadioButton, option, painter, widget)
            return
        if element in (pe.PE_IndicatorArrowDown, pe.PE_IndicatorSpinDown):
            self._draw_chevron("chevron-down", option, painter, widget)
            return
        if element in (pe.PE_IndicatorArrowUp, pe.PE_IndicatorSpinUp):
            self._draw_chevron("chevron-up", option, painter, widget)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_chevron(name: str, option, painter, widget) -> None:
        m = material_of(widget)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        r = option.rect
        size = min(12.0, min(r.width(), r.height()) + 2.0)
        cx, cy = r.center().x() + 0.5, r.center().y() + 0.5
        color = m.text2 if enabled else m.text3
        paint_icon(painter, name, QRectF(cx - size / 2, cy - size / 2, size, size), color, 2.0)

    @staticmethod
    def _draw_choice(radio: bool, option, painter, widget) -> None:
        m = material_of(widget)
        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        on = bool(state & QStyle.StateFlag.State_On)
        partial = bool(state & QStyle.StateFlag.State_NoChange)
        hover = bool(state & QStyle.StateFlag.State_MouseOver) and enabled
        sunken = bool(state & QStyle.StateFlag.State_Sunken)
        r = QRectF(option.rect)
        side = min(r.width(), r.height()) - 2
        box = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
        radius = side / 2 if radio else 5.5
        painter.save()
        if on or partial:
            fill = m.accent if not sunken else mix(m.accent, _BLACK, 0.14)
            paint_wash(painter, box, radius, dim(fill, enabled, 0.5))
            if radio:
                dot = QRectF(box.center().x() - 3, box.center().y() - 3, 6, 6)
                paint_wash(painter, dot, 3, dim(m.on_accent, enabled, 0.6))
            else:
                icon = "minus" if partial else "check"
                paint_icon(
                    painter,
                    icon,
                    box.adjusted(2.2, 2.2, -2.2, -2.2),
                    dim(m.on_accent, enabled, 0.6),
                    2.8,
                )
        else:
            fill = m.fill_press if (hover or sunken) else mix(m.fill_hover, m.fill_press, 0.4)
            paint_wash(painter, box, radius, dim(fill, enabled))
            paint_control_rim(painter, box, radius, m, 1.0 if enabled else 0.5)
            if m.dark is False:
                paint_wash(painter, box, radius, QColor(0, 0, 0, 0), dim(m.divider, enabled))
        painter.restore()


_holder: dict[str, GlassStyle] = {}


def apply_app_style(app: QApplication, material: Material, *, widget_style: bool = True) -> None:
    """Application font, palette and stylesheet from the tokens; GlassStyle unless declined."""
    if widget_style and "style" not in _holder:
        _holder["style"] = GlassStyle()
        app.setStyle(_holder["style"])
    app.setFont(type_font("body"))
    app.setPalette(palette_for(material))
    app.setStyleSheet(build_qss(material))


__all__ = ["GlassStyle", "apply_app_style", "build_qss", "css", "palette_for", "surface"]
