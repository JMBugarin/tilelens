import sys

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
    assert "Screen Recording" in adapter.permission_help()
    adapter.close()


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
