"""Regenerate src/tilelens/vision/priors.npz: letter shapes in common bold sans-serif fonts.

TileLens uses them to recognise a letter it has not yet seen in the game's own font. Only the
resulting 24x24 shape vectors are stored, never the fonts. Run with:

    QT_QPA_PLATFORM=offscreen uv run python tools/make_priors.py
"""

from __future__ import annotations

import os
import string
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter

from tilelens.vision.cells import mask_to_vec

FONTS = [
    "Tahoma",
    "Verdana",
    "Arial",
    "Trebuchet MS",
    "Avenir Next",
    "Arial Rounded MT Bold",
    "DIN Alternate",
    "Chalkboard",
]
OUT = Path(__file__).resolve().parents[1] / "src/tilelens/vision/priors.npz"
PX = 192


def render(font: QFont, ch: str) -> np.ndarray:
    img = QImage(PX, PX, QImage.Format.Format_Grayscale8)
    img.fill(0)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(img.rect(), 0x84, ch)
    painter.end()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(PX, img.bytesPerLine())[:, :PX]
    return arr > 127


def main() -> None:
    app = QGuiApplication([])
    have = set(QFontDatabase.families())
    names, vecs = [], []
    for family in FONTS:
        if family not in have:
            print("missing font:", family)
            continue
        font = QFont(family, 60)
        font.setBold(True)
        names.append(family)
        vecs.append([mask_to_vec(render(font, ch)) for ch in string.ascii_uppercase])
    arr = np.array(vecs, np.float16)
    digits = []
    for family in names:
        font = QFont(family, 60)
        font.setBold(True)
        digits.append([mask_to_vec(render(font, ch)) for ch in string.digits])
    np.savez_compressed(
        OUT,
        letters=np.array(list(string.ascii_uppercase)),
        vecs=arr,
        digits=np.array(list(string.digits)),
        digit_vecs=np.array(digits, np.float16),
    )
    print(f"wrote {OUT} ({len(names)} fonts: {', '.join(names)})")
    del app


if __name__ == "__main__":
    main()
