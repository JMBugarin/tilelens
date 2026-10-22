from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tilelens.capture.source import CallableSource
from tilelens.platforms import UnsupportedPlatform, get_adapter
from tilelens.synth.game import calibration_samples
from tilelens.ui.auto_calibrate import AutoCalibrationDialog
from tilelens.ui.calibrate_dialog import CalibrationDialog
from tilelens.ui.main_window import MainWindow
from tilelens.ui.region_select import RegionSelector
from tilelens.ui.session import LiveSession
from tilelens.ui.settings import Settings
from tilelens.vision.recognizer import calibrate


class App:
    def __init__(self, settings: Settings, demo: bool = False, demo_seed: int = 3):
        self.settings = settings
        self.demo = demo
        self.fake = None
        adapter = None
        if demo:
            from tilelens.ui.fakegame import FakeGameWindow

            self.fake = FakeGameWindow(seed=demo_seed)
            self.fake.show()
            self.session = LiveSession(
                settings,
                adapter=None,
                source=CallableSource(self.fake.current_frame),
                region_provider=self.fake.capture_region,
            )
        else:
            try:
                adapter = get_adapter()
            except UnsupportedPlatform as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(2) from None
            self.session = LiveSession(settings, adapter=adapter)
        self.window = MainWindow(self.session)
        self.window.region_btn.clicked.connect(self.select_region)
        self.window.cal_btn.clicked.connect(self.calibrate)
        if demo:
            self.window.region_btn.setEnabled(False)
            self.demo_calibrate()
        elif adapter is not None and adapter.screen_permission() is False:
            adapter.request_permission()
            self.window.show_message(adapter.permission_help())
        self.window.show()
        if adapter is not None:
            adapter.show_on_all_spaces(int(self.window.winId()))
        self.session.start()

    def demo_calibrate(self) -> None:
        samples, hint = calibration_samples(
            range(71, 79), theme=self.fake.game.theme, cell=self.fake.game.cell
        )
        cal = calibrate(samples, self.session.profile, board_hint=hint)
        self.session.recognizer = None
        self.session.set_calibration(cal)

    def select_region(self) -> None:
        self.selector = RegionSelector()
        self.selector.selected.connect(self._region_chosen)
        self.selector.show()
        self.selector.activateWindow()

    def _region_chosen(self, region) -> None:
        self.session.set_region(region)
        self.window.show_message("Region saved. Now calibrate.")

    def calibrate(self) -> CalibrationDialog | AutoCalibrationDialog | None:
        frame = self.session.source_frame()
        if frame is None:
            self.window.show_message(
                "Nothing captured. Select the region first"
                + (" and check the Screen Recording permission." if not self.demo else ".")
            )
            return None
        board, rack = None, ""
        ctrl = self.session.controller
        if ctrl is not None and ctrl.last_confirmed is not None:
            board, _, order = ctrl.last_confirmed
            rack = "".join(order)
        previous = self.session.recognizer.cal if self.session.recognizer else None
        if (
            self.session.profile.appearance is not None
            and self.session.profile.appearance.rack_tile
        ):
            dlg = AutoCalibrationDialog(
                frame,
                self.session.profile,
                self.session.source_frame,
                rack=rack,
                previous=previous,
                parent=self.window,
            )
            if dlg.exec() and dlg.result is not None:
                self.session.set_calibration(dlg.result)
                self.window.show_message("")
            return dlg
        dlg = CalibrationDialog(
            frame,
            self.session.profile,
            self.session.source_frame,
            board=board,
            rack=rack,
            previous=previous,
            parent=self.window,
        )
        if dlg.exec() and dlg.result is not None:
            self.session.set_calibration(dlg.result)
            self.window.show_message("")
        return dlg

    def shutdown(self) -> None:
        if not self.demo:
            self.settings.save()
        self.session.stop()
        if self.fake is not None:
            self.fake.close()


def run(argv: list[str] | None = None, demo: bool = False) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv[:1])
    app.setApplicationName("TileLens")
    if demo:
        import tempfile
        from pathlib import Path

        settings = Settings(calibration=str(Path(tempfile.gettempdir()) / "tilelens-demo-cal.json"))
    else:
        settings = Settings.load()
    shell = App(settings, demo=demo)
    app.aboutToQuit.connect(shell.shutdown)
    import signal

    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    if shell.session.snapshot is None:
        shell.window.show_snapshot(None)
    return app.exec()


__all__ = ["App", "run"]
