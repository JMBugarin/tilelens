from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from tilelens.capture.source import PermissionGate, ScreenSource
from tilelens.paths import calibration_dir
from tilelens.platforms import PlatformAdapter, Region
from tilelens.rules.lexicon import Lexicon, WordListError, lexicon_for
from tilelens.rules.profile import Profile, ProfileError, demo_profile_dir, load_profile
from tilelens.state.controller import LiveController, Snapshot, Status
from tilelens.state.worker import InlineSolver, ProcessSolver
from tilelens.ui.overlay import OverlayWindow
from tilelens.ui.preview import CellMapper
from tilelens.ui.settings import Settings
from tilelens.vision.recognizer import Calibration, Recognizer


class SuppressingSource:
    """Hides the overlay's drawing around each capture when it cannot be excluded."""

    def __init__(self, inner, overlay: OverlayWindow):
        self.inner = inner
        self.overlay = overlay

    def grab(self):
        self.overlay.begin_capture()
        try:
            return self.inner.grab()
        finally:
            self.overlay.end_capture()


class LiveSession(QObject):
    updated = Signal(object)
    message = Signal(str)

    def __init__(
        self,
        settings: Settings,
        adapter: PlatformAdapter | None = None,
        source=None,
        region_provider: Callable[[], Region | None] | None = None,
        use_process: bool = True,
        overlay: OverlayWindow | None = None,
    ):
        super().__init__()
        self.settings = settings
        self.adapter = adapter
        self._custom_source = source
        self.region_provider = region_provider or (lambda: self.settings.region_obj)
        self.use_process = use_process
        self.profile: Profile = load_profile(
            settings.profile or demo_profile_dir() / "demo_garden.toml"
        )
        self.lexicon: Lexicon | None = None
        self.solver = None
        self.controller: LiveController | None = None
        self.recognizer: Recognizer | None = None
        self.overlay = overlay if overlay is not None else OverlayWindow()
        self.preview_hidden = False
        self.selected = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self._load_words()
        self._load_calibration()
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._screens_changed)
            app.screenRemoved.connect(self._screens_changed)
            for screen in app.screens():
                self._watch_screen(screen)

    # setup

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._screens_changed)
        screen.logicalDotsPerInchChanged.connect(self._screens_changed)

    def _screens_changed(self, *args) -> None:
        app = QGuiApplication.instance()
        for screen in app.screens():
            with contextlib.suppress(RuntimeError):
                self._watch_screen(screen)
        self.refresh_overlay()

    def _load_words(self) -> None:
        try:
            self.lexicon = lexicon_for(self.profile, self.settings.words)
        except WordListError as exc:
            self.lexicon = None
            self.message.emit(str(exc))

    def calibration_path(self) -> Path:
        if self.settings.calibration:
            return Path(self.settings.calibration)
        return calibration_dir() / f"{self.profile.fingerprint()}.json"

    def _load_calibration(self) -> None:
        path = self.calibration_path()
        if not path.is_file():
            return
        try:
            cal = Calibration.load(path)
        except (OSError, ValueError, KeyError):
            self.message.emit("Saved calibration could not be read; please recalibrate.")
            return
        if cal.profile_fingerprint != self.profile.fingerprint():
            self.message.emit("Saved calibration is for a different profile; please recalibrate.")
            return
        self.recognizer = Recognizer(cal, self.profile)

    def set_calibration(self, cal: Calibration) -> None:
        cal.save(self.calibration_path())
        self.settings.calibration = str(self.calibration_path())
        self.recognizer = Recognizer(cal, self.profile)
        if self.controller is not None:
            self.controller.set_recognizer(self.recognizer)
        else:
            self.start()

    def set_region(self, region: Region) -> None:
        self.settings.region = region.to_dict()
        if self.controller is not None:
            self.controller.source = self._make_source()
            self.controller.rescan()
        self.refresh_overlay()

    def source_frame(self) -> np.ndarray | None:
        return self._make_source().grab()

    def _make_source(self):
        if self._custom_source is not None:
            src = self._custom_source
        else:
            region = self.settings.region_obj
            if region is None or self.adapter is None:
                return _NullSource()
            src = PermissionGate(ScreenSource(self.adapter, region), self.adapter)
        if self.settings.overlay_mode == "suppress":
            return SuppressingSource(src, self.overlay)
        return src

    @property
    def ready_to_run(self) -> bool:
        return self.lexicon is not None and self.recognizer is not None

    def start(self) -> bool:
        if not self.ready_to_run:
            return False
        if self.solver is None:
            if self.use_process and self.profile.source is not None:
                self.solver = ProcessSolver(self.profile.source, self.settings.words)
            else:
                from tilelens.solver.movegen import MoveGenerator

                self.solver = InlineSolver(
                    MoveGenerator(self.profile, self.lexicon, use_cache=True)
                )
        self.controller = LiveController(
            self._make_source(),
            self.recognizer,
            self.solver,
            self.profile,
            self.lexicon.version,
            hz=self.settings.hz,
            on_update=self._on_update,
        )
        if self.settings.overlay_mode == "exclude" and self.adapter is not None:
            region = self.region_provider()
            if region:
                self.overlay.place(region)
            if not self.overlay.try_exclude_from_capture(self.adapter):
                self.message.emit(
                    "The preview window could not be hidden from screen capture; "
                    "switching to hiding it during each capture."
                )
                self.settings.overlay_mode = "suppress"
                self.controller.source = self._make_source()
        self.timer.start(0)
        return True

    def reload(self) -> None:
        """Re-read the profile, word list and calibration after a settings change."""
        self.timer.stop()
        if self.solver is not None:
            self.solver.close()
            self.solver = None
        self.controller = None
        self.overlay.clear()
        self.overlay.hide()
        try:
            self.profile = load_profile(
                self.settings.profile or demo_profile_dir() / "demo_garden.toml"
            )
        except ProfileError as exc:
            self.message.emit(f"Profile problem: {exc}")
            return
        self.recognizer = None
        self._load_words()
        self._load_calibration()
        self.start()
        self.updated.emit(self.snapshot)

    def stop(self) -> None:
        self.timer.stop()
        if self.solver is not None:
            self.solver.close()
            self.solver = None
        self.overlay.close()
        if self.adapter is not None:
            self.adapter.close()

    # loop

    def _tick(self) -> None:
        if self.controller is None:
            return
        self.controller.tick()
        self.timer.start(int(self.controller.interval() * 1000))

    def _on_update(self, snap: Snapshot) -> None:
        if snap.result is None or snap.status != Status.READY:
            self.selected = 0
        self.refresh_overlay(snap)
        self.updated.emit(snap)

    # actions

    def toggle_pause(self) -> bool:
        if self.controller is None:
            return False
        if self.controller.paused:
            self.controller.resume()
        else:
            self.controller.pause()
        return self.controller.paused

    def rescan(self) -> None:
        if self.controller is not None:
            self.controller.rescan()

    def set_preview_hidden(self, hidden: bool) -> None:
        self.preview_hidden = hidden
        self.refresh_overlay()

    def select(self, index: int) -> None:
        self.selected = index
        self.refresh_overlay()

    def next_card(self) -> None:
        snap = self.snapshot
        if snap and snap.result and snap.result.moves:
            self.select((self.selected + 1) % len(snap.result.moves))

    @property
    def snapshot(self) -> Snapshot | None:
        return None if self.controller is None else self.controller.snapshot

    def selected_move(self):
        snap = self.snapshot
        if snap is None or snap.status != Status.READY or snap.result is None:
            return None
        moves = snap.result.moves
        return moves[self.selected] if 0 <= self.selected < len(moves) else None

    def overlay_mapper(self) -> CellMapper | None:
        ctrl = self.controller
        region = self.region_provider()
        if ctrl is None or region is None or ctrl.last_grid is None or not ctrl.last_frame_size:
            return None
        return CellMapper.for_overlay(ctrl.last_grid, ctrl.last_frame_size, region)

    def refresh_overlay(self, snap: Snapshot | None = None) -> None:
        move = self.selected_move()
        if self.preview_hidden or self.settings.overlay_mode == "panel_only" or move is None:
            self.overlay.clear()
            self.overlay.hide()
            return
        region = self.region_provider()
        mapper = self.overlay_mapper()
        if region is None or mapper is None:
            self.overlay.clear()
            return
        self.overlay.place(region)
        self.overlay.show_move(move, mapper)
        if not self.overlay.isVisible():
            self.overlay.show()


class _NullSource:
    def grab(self):
        return None
