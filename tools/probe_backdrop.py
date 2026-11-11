"""Manual check that apply_backdrop really blurs what is behind a glass window (macOS).

Needs a desktop session and the Screen Recording permission. It opens a striped window, puts a
frameless translucent window with a native backdrop over it, photographs the screen and compares
how much fine detail survives inside the glass, outside it and in its rounded corners.

    uv run python tools/probe_backdrop.py --out /tmp/backdrop.png [--light] [--radius 60]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from tilelens.platforms import Region, get_adapter
from tilelens.platforms import macos as mac

BACK = QRect(0, 0, 760, 520)
GLASS = QRect(190, 110, 380, 300)
BLEED = 24
MARKER = QColor(230, 60, 60)
MARKER_RECT = QRect(40, 40, 40, 40)
FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
    | Qt.WindowType.NoDropShadowWindowHint
)


class Stripes(QWidget):
    def __init__(self):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        for x in range(0, self.width() // 2, 2):
            p.fillRect(x, 0, 1, self.height(), QColor(0, 0, 0))


class Glass(QWidget):
    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(MARKER_RECT.translated(BLEED, BLEED), MARKER)


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def screenshot(path: Path) -> np.ndarray:
    r = BACK
    subprocess.run(
        [
            "screencapture",
            "-x",
            "-t",
            "png",
            "-R",
            f"{r.x()},{r.y()},{r.width()},{r.height()}",
            str(path),
        ],
        check=True,
    )
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("screencapture produced no image (Screen Recording permission?)")
    return img


def detail(gray: np.ndarray, mask: np.ndarray) -> float:
    """Mean absolute horizontal gradient over pixels whose right neighbour is also in the mask."""
    gx = np.abs(np.diff(gray.astype(np.float32), axis=1))
    both = mask[:, 1:] & mask[:, :-1]
    return float(gx[both].mean()) if both.any() else float("nan")


class Measure:
    def __init__(self, img: np.ndarray, radius: float, glass: QRect):
        self.img = img
        self.gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self.s = img.shape[1] / BACK.width()
        self.radius = radius
        self.glass = glass

    def px(self, r: QRect) -> tuple[slice, slice]:
        s = self.s
        return (
            slice(round(r.top() * s), round(r.bottom() * s)),
            slice(round(r.left() * s), round(r.right() * s)),
        )

    def rect_mask(self, r: QRect) -> np.ndarray:
        m = np.zeros(self.gray.shape, bool)
        m[self.px(r)] = True
        return m

    def core(self) -> np.ndarray:
        g = self.glass
        left = QRect(g.x() + 30, g.y() + 30, BACK.width() // 2 - g.x() - 60, g.height() - 60)
        marker = MARKER_RECT.translated(g.x(), g.y()).adjusted(-8, -8, 8, 8)
        return self.rect_mask(left) & ~self.rect_mask(marker)

    def edge_width(self) -> tuple[float, float, float]:
        """Plateau levels either side of the stripe/solid boundary and its 10-90% rise in points."""
        g, s = self.glass, self.s
        mid = BACK.width() // 2
        rows = slice(round((g.y() + 100) * s), round((g.y() + 200) * s))
        prof = self.gray[rows].astype(np.float32).mean(axis=0)
        lo = prof[round((g.x() + 30) * s) : round((mid - 70) * s)].mean()
        hi = prof[round((mid + 70) * s) : round((g.right() - 30) * s)].mean()
        span = hi - lo
        if abs(span) < 10:
            return float(lo), float(hi), float("nan")
        seg = prof[round((mid - 70) * s) : round((mid + 70) * s)]
        frac = (seg - lo) / span
        a, b = np.argmax(frac >= 0.1), np.argmax(frac >= 0.9)
        return float(lo), float(hi), float((b - a) / s)

    def outside(self) -> np.ndarray:
        return self.rect_mask(QRect(10, 150, 140, 220))

    def bleed(self) -> np.ndarray:
        g = self.glass
        return self.rect_mask(QRect(g.x() - BLEED + 4, g.y() + 30, BLEED - 8, g.height() - 60))

    def corners(self) -> np.ndarray:
        s, r, g = self.s, self.radius * self.s, self.glass
        h, w = self.gray.shape
        yy, xx = np.mgrid[0:h, 0:w]
        out = np.zeros((h, w), bool)
        x0, y0 = g.left() * s, g.top() * s
        y1 = (g.bottom() + 1) * s
        for ax, ay, sx, sy in ((x0, y0, 1, 1), (x0, y1, 1, -1)):
            cx, cy = ax + sx * r, ay + sy * r
            in_square = ((xx - ax) * sx >= 1) & ((xx - ax) * sx < r) & ((yy - ay) * sy >= 1)
            in_square &= (yy - ay) * sy < r
            out |= in_square & (np.hypot(xx - cx, yy - cy) > r + 3)
        return out

    def report(self) -> dict:
        core, outside = self.core(), self.outside()
        return {
            "detail_glass": detail(self.gray, core),
            "detail_outside": detail(self.gray, outside),
            "detail_corners": detail(self.gray, self.corners()),
            "detail_bleed": detail(self.gray, self.bleed()),
            "mean_gray_glass": float(self.gray[core].mean()),
            "mean_gray_outside": float(self.gray[outside].mean()),
        }

    def marker_bgr(self) -> tuple[int, int, int]:
        c = MARKER_RECT.translated(self.glass.x(), self.glass.y()).center()
        y, x = round(c.y() * self.s), round(c.x() * self.s)
        return tuple(int(v) for v in self.img[y, x])


def objc_state(win_id: int) -> dict:
    import objc
    from AppKit import (
        NSStatusWindowLevel,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowSharingNone,
    )

    window = objc.objc_object(c_void_p=int(win_id)).window()
    behavior = int(window.collectionBehavior())
    want = (
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    return {
        "level_is_status": int(window.level()) == NSStatusWindowLevel,
        "all_spaces_and_fullscreen_aux": behavior & want == want,
        "sharing_none": int(window.sharingType()) == NSWindowSharingNone,
        "is_opaque": bool(window.isOpaque()),
    }


def backdrop_views(win_id: int) -> list:
    import objc

    window = objc.objc_object(c_void_p=int(win_id)).window()
    frame_view = window.contentView().superview()
    return [v for v in frame_view.subviews() if v.identifier() == mac.BACKDROP_ID]


def frame_matches(win_id: int, inset: float) -> bool:
    import objc

    window = objc.objc_object(c_void_p=int(win_id)).window()
    content = window.contentView().frame()
    (view,) = backdrop_views(win_id)
    f = view.frame()
    want = (inset, inset, content.size.width - 2 * inset, content.size.height - 2 * inset)
    got = (f.origin.x, f.origin.y, f.size.width, f.size.height)
    return all(abs(a - b) < 0.5 for a, b in zip(want, got, strict=True))


def fmt(d: dict) -> str:
    return " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in d.items())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "backdrop.png"))
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--radius", type=float, default=60.0)
    ap.add_argument("--material", default=mac.MATERIAL)
    args = ap.parse_args()
    mac.MATERIAL = args.material
    dark = not args.light
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.TemporaryDirectory()
    work = Path(scratch.name)

    app = QApplication([])
    screen = app.primaryScreen().availableGeometry()
    BACK.moveTopLeft(screen.topLeft() + QPoint(60, 60))
    glass_rect = GLASS.translated(BACK.topLeft())
    adapter = get_adapter()
    checks: dict[str, bool] = {}

    back = Stripes()
    back.setGeometry(BACK)
    back.show()
    pump(app, 0.4)

    glass = Glass()
    glass.setGeometry(glass_rect.adjusted(-BLEED, -BLEED, BLEED, BLEED))
    glass.show()
    wid = int(glass.winId())
    local = GLASS

    try:
        checks["show_on_all_spaces"] = adapter.show_on_all_spaces(wid)
        before = Measure(screenshot(work / "before.png"), args.radius, local)
        checks["apply_backdrop"] = adapter.apply_backdrop(wid, args.radius, dark, BLEED)
        pump(app, 1.0)
        m = Measure(screenshot(out), args.radius, local)
        r = m.report()
        rb = before.report()
        print(f"before backdrop: {fmt(rb)}")
        print(f"dark={dark} radius={args.radius} material={mac.MATERIAL}")
        print(f"after  backdrop: {fmt(r)}")
        factor = r["detail_outside"] / max(r["detail_glass"], 1e-6)
        print(f"blur factor (outside/glass): {factor:.1f}")
        lo, hi, rise = m.edge_width()
        print(
            f"stripe/solid boundary behind glass: plateaus {lo:.0f} -> {hi:.0f}, "
            f"10-90% rise {rise:.1f} pt"
        )
        _, _, rise_before = before.edge_width()
        print(f"same boundary before backdrop: rise {rise_before:.1f} pt")
        checks["boundary_is_smoothed"] = rise == rise and rise >= 8.0
        print(f"marker BGR over glass: {m.marker_bgr()} (painted RGB {MARKER.getRgb()[:3]})")
        checks["blur_factor>=20"] = factor >= 20
        checks["corners_sharp"] = r["detail_corners"] >= 0.7 * r["detail_outside"]
        checks["bleed_sharp"] = r["detail_bleed"] >= 0.7 * r["detail_outside"]
        checks["no_blur_before"] = rb["detail_glass"] >= 0.7 * rb["detail_outside"]
        mb, mg, mr = m.marker_bgr()
        checks["qt_paint_above_blur"] = (
            abs(mr - 230) < 20 and abs(mg - 60) < 20 and abs(mb - 60) < 20
        )

        state = objc_state(wid)
        print(f"window state after apply: {fmt(state)}")
        checks["level_and_spaces_kept"] = (
            state["level_is_status"] and state["all_spaces_and_fullscreen_aux"]
        )
        checks["window_not_opaque"] = not state["is_opaque"]

        for _ in range(3):
            adapter.apply_backdrop(wid, args.radius, dark, BLEED)
        checks["idempotent_one_view"] = len(backdrop_views(wid)) == 1

        glass.hide()
        pump(app, 0.4)
        glass.show()
        adapter.show_on_all_spaces(wid)
        pump(app, 1.0)
        again = Measure(screenshot(work / "reshown.png"), args.radius, local).report()
        print(f"after hide, show and show_on_all_spaces: {fmt(again)}")
        checks["survives_reshow"] = (
            len(backdrop_views(wid)) == 1 and again["detail_glass"] < r["detail_outside"] / 20
        )

        adapter.apply_backdrop(wid, args.radius, not dark, BLEED)
        pump(app, 0.8)
        flipped = Measure(screenshot(work / "flipped.png"), args.radius, local).report()
        print(f"flipped appearance: {fmt(flipped)}")
        delta = flipped["mean_gray_glass"] - r["mean_gray_glass"]
        checks["appearance_follows_dark"] = delta < -15 if not dark else delta > 15
        adapter.apply_backdrop(wid, args.radius, dark, BLEED)

        wide = glass.geometry().adjusted(0, 0, 70, 30)
        glass.setGeometry(wide)
        pump(app, 1.0)
        grown = QRect(local.x(), local.y(), local.width() + 70, local.height() + 30)
        g = Measure(screenshot(work / "resized.png"), args.radius, grown).report()
        print(f"after resize to {grown.width()}x{grown.height()}: {fmt(g)}")
        checks["resize_follows"] = g["detail_glass"] < r["detail_outside"] / 20 and frame_matches(
            wid, BLEED
        )
        glass.setGeometry(glass_rect.adjusted(-BLEED, -BLEED, BLEED, BLEED))
        pump(app, 0.6)

        checks["exclude_from_capture"] = adapter.exclude_from_capture(wid)
        pump(app, 0.6)
        checks["still_excluded"] = objc_state(wid)["sharing_none"]
        shot = Measure(screenshot(work / "excluded.png"), args.radius, local)
        e = shot.report()
        print(f"screencapture while excluded: {fmt(e)}")
        checks["excluded_from_screencapture"] = e["detail_glass"] >= 0.7 * e["detail_outside"]
        adapter.include_in_capture(int(back.winId()))
        frame = adapter.capture(Region(BACK.x(), BACK.y(), BACK.width(), BACK.height()))
        a = Measure(frame, args.radius, local).report()
        print(f"adapter.capture while excluded: {fmt(a)} frame={frame.shape}")
        checks["excluded_from_adapter_capture"] = a["detail_glass"] >= 0.7 * a["detail_outside"]
        state = objc_state(wid)
        checks["level_and_spaces_kept_after_exclude"] = (
            state["level_is_status"] and state["all_spaces_and_fullscreen_aux"]
        )

        import objc
        from AppKit import NSWindowSharingReadOnly

        objc.objc_object(c_void_p=wid).window().setSharingType_(NSWindowSharingReadOnly)
        checks["clear_backdrop"] = adapter.clear_backdrop(wid)
        checks["clear_twice_false"] = not adapter.clear_backdrop(wid)
        checks["no_view_left"] = len(backdrop_views(wid)) == 0
        pump(app, 0.8)
        c = Measure(screenshot(work / "cleared.png"), args.radius, local).report()
        print(f"after clear_backdrop: {fmt(c)}")
        checks["sharp_after_clear"] = c["detail_glass"] >= 0.7 * c["detail_outside"]
    finally:
        glass.close()
        back.close()
        app.processEvents()
        adapter.close()
        scratch.cleanup()

    width = max(len(k) for k in checks)
    for k, v in checks.items():
        print(f"{'PASS' if v else 'FAIL'}  {k:<{width}}")
    print(f"saved {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
