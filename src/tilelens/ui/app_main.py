from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

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
        self.session.panel.calibrate_clicked.connect(self.calibrate)
        self.adapter = adapter
        if demo:
            self.window.region_btn.setEnabled(False)
            self.demo_calibrate()
        elif adapter is not None and adapter.screen_permission() is False:
            adapter.request_permission()
            self.window.show_message(adapter.permission_help())
        self.window.resize(520, 400)
        self.window.show()
        QTimer.singleShot(0, self._park_window)
        started = self.session.start()
        self._make_tray()
        self.session.panel.settings_clicked.connect(self.show_settings)
        if not demo and self.session.profile.appearance is not None:
            self._auto = QTimer()
            self._auto.timeout.connect(self._auto_start)
            self._auto.start(2500)
            self._auto_start()
        if started and not demo:
            # the floating panel is the interface while playing; the window would cover the game
            QTimer.singleShot(1500, self.window.hide)

    def _auto_start(self) -> None:
        """Without a calibration, keep trying: as soon as the game is on screen, learn it."""
        if self.session.recognizer is not None or self.session.lexicon is None:
            if self.session.recognizer is not None:
                self._auto.stop()
            return
        if not self.session.auto_calibrate() and not self.session.panel.isVisible():
            self.session.show_setup_panel()

    def _make_tray(self) -> None:
        pix = QPixmap(32, 32)
        pix.fill(QColor("#22d3ee"))
        self.tray = QSystemTrayIcon(QIcon(pix))
        menu = QMenu()
        menu.addAction("Show settings", self.show_settings)
        menu.addAction("Pause / resume", self.session.toggle_pause)
        menu.addAction("Rescan", self.session.rescan)
        menu.addSeparator()
        menu.addAction("Quit TileLens", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("TileLens")
        self.tray.show()
        self._menu = menu

    def show_settings(self) -> None:
        self._float(self.window)
        self.window.show()
        self.window.raise_()

    def _park_window(self) -> None:
        # bottom-left corner, clear of the game's rack and board
        screen = self.window.screen().availableGeometry()
        self.window.move(
            screen.left() + 8, screen.bottom() - self.window.frameGeometry().height() - 8
        )

    def demo_calibrate(self) -> None:
        samples, hint = calibration_samples(
            range(71, 79), theme=self.fake.game.theme, cell=self.fake.game.cell
        )
        cal = calibrate(samples, self.session.profile, board_hint=hint)
        self.session.recognizer = None
        self.session.set_calibration(cal)

    def _float(self, dlg) -> None:
        """Open over the game, even when it is full screen in its own Space."""
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dlg.setWindowFlag(Qt.WindowType.Tool, True)
        dlg.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        if self.adapter is not None:
            self.adapter.show_on_all_spaces(int(dlg.winId()))

    def select_region(self) -> None:
        self.selector = RegionSelector()
        self.selector.selected.connect(self._region_chosen)
        self.selector.show()
        self.selector.activateWindow()

    def _region_chosen(self, region) -> None:
        self.session.set_region(region)
        self.window.show_message("Region saved. Now calibrate.")

    def calibrate(self) -> CalibrationDialog | AutoCalibrationDialog | None:
        if not self.demo and self.session.profile.appearance is not None:
            if self.session.auto_calibrate():
                self.window.show_message("")
                return None
            if self.session.needs_letters is None:
                return None  # the reason is in the panel
            rack = self.session.needs_letters.guess
        else:
            rack = ""
        frame = self.session.find_board_screen()
        if frame is None:
            self.window.show_message(
                "Nothing captured. Select the region first"
                + (" and check the Screen Recording permission." if not self.demo else ".")
            )
            return None
        board = None
        ctrl = self.session.controller
        if ctrl is not None and ctrl.last_confirmed is not None:
            board, _, order = ctrl.last_confirmed
            rack = rack or "".join(order)
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
            )
            self._float(dlg)
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
