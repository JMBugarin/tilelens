import os
import subprocess
import sys
import textwrap

import numpy as np
import pytest

from tilelens.capture.source import ReplaySource, ScreenSource
from tilelens.platforms import GenericAdapter, Region, UnsupportedPlatform, get_adapter
from tilelens.platforms.base import CaptureError, PlatformAdapter


def test_windows_is_unsupported_with_clear_message():
    with pytest.raises(UnsupportedPlatform, match="Windows is not supported"):
        get_adapter("win32")


def test_region_round_trip():
    r = Region(10, 20, 300, 400)
    assert Region.from_dict(r.to_dict()) == r


def test_screen_source_reports_capture_errors():
    class Broken(PlatformAdapter):
        def capture(self, region):
            raise CaptureError("denied")

        def screen_permission(self):
            return False

    src = ScreenSource(Broken(), Region(0, 0, 10, 10))
    assert src.grab() is None and src.last_error == "denied"


def test_replay_source_holds_last_frame():
    a, b = np.zeros((2, 2, 3), np.uint8), np.ones((2, 2, 3), np.uint8)
    src = ReplaySource([a, b])
    assert src.grab() is a and src.grab() is b and src.grab() is b
    assert ReplaySource([]).grab() is None


def test_empty_region_is_a_capture_error():
    with pytest.raises(CaptureError):
        GenericAdapter().capture(Region(0, 0, 0, 10))


@pytest.mark.gui
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS adapter")
def test_macos_adapter_captures_and_reports_permission():
    adapter = get_adapter()
    assert adapter.name == "macos"
    assert adapter.screen_permission() in (True, False)
    frame = adapter.capture(Region(0, 0, 64, 48))
    assert frame.ndim == 3 and frame.shape[2] == 3 and frame.shape[0] >= 48
    from AppKit import NSScreen

    scale = NSScreen.mainScreen().backingScaleFactor()
    assert frame.shape[:2] == (round(48 * scale), round(64 * scale))
    assert "Screen Recording" in adapter.permission_help()
    adapter.close()


def test_default_adapter_has_no_backdrop():
    class Plain(PlatformAdapter):
        def capture(self, region):
            return np.zeros((1, 1, 3), np.uint8)

        def screen_permission(self):
            return None

    plain = Plain()
    assert plain.apply_backdrop(0x20000, 26.0, True) is False
    assert plain.apply_backdrop(0x20000, 26.0, False, 24.0) is False
    assert plain.clear_backdrop(0x20000) is False


@pytest.mark.gui
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS adapter")
def test_macos_backdrop_on_a_real_window():
    code = textwrap.dedent(
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QWidget
        from tilelens.platforms import get_adapter

        app = QApplication([])
        w = QWidget(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.setGeometry(40, 120, 220, 140)
        w.show()
        app.processEvents()
        a, wid = get_adapter(), int(w.winId())
        import objc
        window = objc.objc_object(c_void_p=wid).window()
        def count():
            return sum(1 for v in window.contentView().superview().subviews() if v.identifier())
        out = [a.show_on_all_spaces(wid), a.apply_backdrop(wid, 26, True, 20)]
        out += [a.apply_backdrop(wid, 18, False, 10), count() == 1]
        w.resize(300, 200)
        app.processEvents()
        out += [a.exclude_from_capture(wid), a.clear_backdrop(wid), count() == 0]
        out += [a.clear_backdrop(wid) is False, a.apply_backdrop(3, 26, True) is False]
        print("RESULT", out)
        w.close()
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
    )
    assert "RESULT [True, True, True, True, True, True, True, True, True]" in proc.stdout, (
        proc.stdout + proc.stderr
    )


@pytest.mark.gui
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS adapter")
def test_glass_window_gets_a_point_sized_inset_blur_from_the_adapter():
    code = textwrap.dedent(
        """
        import objc
        from PySide6.QtWidgets import QApplication
        from tilelens.platforms import get_adapter
        from tilelens.ui.design import GlassWindow
        from tilelens.ui.design.material import SHADOW_BLEED

        app = QApplication([])
        adapter = get_adapter()
        GlassWindow.backdrop = adapter
        w = GlassWindow()
        w.resize(380 + 2 * SHADOW_BLEED, 300 + 2 * SHADOW_BLEED)
        w.show()
        app.processEvents()
        wid = int(w.winId())
        window = objc.objc_object(c_void_p=wid).window()

        def effects():
            return [
                v for v in window.contentView().superview().subviews()
                if v.identifier() == "tilelens.backdrop"
            ]

        def rect(view):
            f = view.frame()
            return (f.origin.x, f.origin.y, f.size.width, f.size.height)

        (view,) = effects()
        out = [w.native_blur, w.devicePixelRatioF() >= 1.0]
        out += [rect(view) == (SHADOW_BLEED, SHADOW_BLEED, 380, 300)]
        out += [view.layer().cornerRadius() == w.radius]
        w.resize(450 + 2 * SHADOW_BLEED, 330 + 2 * SHADOW_BLEED)
        app.processEvents()
        out += [rect(effects()[0]) == (SHADOW_BLEED, SHADOW_BLEED, 450, 330), len(effects()) == 1]
        out += [adapter.clear_backdrop(wid), effects() == []]
        print("RESULT", out)
        w.close()
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
    )
    assert "RESULT [True, True, True, True, True, True, True, True]" in proc.stdout, (
        proc.stdout + proc.stderr
    )


def test_permission_gate_blocks_and_rechecks():
    from tilelens.capture.source import PermissionGate

    class Fake(PlatformAdapter):
        allowed = False

        def capture(self, region):
            return np.zeros((4, 4, 3), np.uint8)

        def screen_permission(self):
            return self.allowed

    clock = {"t": 0.0}
    adapter = Fake()
    gate = PermissionGate(
        ScreenSource(adapter, Region(0, 0, 4, 4)), adapter, clock=lambda: clock["t"], recheck=2.0
    )
    assert gate.grab() is None and gate.last_error == PermissionGate.MISSING
    adapter.allowed = True
    clock["t"] = 1.0
    assert gate.grab() is None
    clock["t"] = 2.5
    assert gate.grab() is not None and gate.last_error is None


def test_controller_reports_missing_permission():
    from tilelens.capture.source import PermissionGate
    from tilelens.state.controller import LiveController, Status
    from tilelens.state.worker import InlineSolver

    class Denied(PlatformAdapter):
        def capture(self, region):
            raise AssertionError("must not capture")

        def screen_permission(self):
            return False

    adapter = Denied()
    gate = PermissionGate(ScreenSource(adapter, Region(0, 0, 4, 4)), adapter)
    ctrl = LiveController(gate, None, InlineSolver(None), None, "v")
    ctrl.tick()
    assert ctrl.snapshot.status == Status.NOT_VISIBLE
    assert "Screen Recording" in ctrl.snapshot.detail


def test_doctor_runs(capsys):
    from tilelens.app import main

    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "screen capture" in out and "numpy" in out
    assert code in (0, 1, 2)


def test_doctor_starts_solver_worker(capsys):
    from tilelens.app import main

    main(["doctor", "--solver"])
    assert "solver worker            ok" in capsys.readouterr().out
