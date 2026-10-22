from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRectF, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from tilelens.capture.source import PermissionGate, ScreenSource
from tilelens.paths import calibration_dir, debug_dump_dir
from tilelens.platforms import PlatformAdapter, Region
from tilelens.rules.lexicon import Lexicon, WordListError, lexicon_for
from tilelens.rules.profile import Profile, ProfileError, demo_profile_dir, load_profile
from tilelens.state.controller import LiveController, Snapshot, Status
from tilelens.state.worker import InlineSolver, ProcessSolver
from tilelens.ui.glass import OverlayScene
from tilelens.ui.glass_panel import GlassPanel
from tilelens.ui.overlay import OverlayWindow
from tilelens.ui.preview import CellMapper
from tilelens.ui.settings import Settings
from tilelens.vision.autoread import ViewReader
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
    selection_changed = Signal(int)

    def __init__(
        self,
        settings: Settings,
        adapter: PlatformAdapter | None = None,
        source=None,
        region_provider: Callable[[], Region | None] | None = None,
        use_process: bool = True,
        overlay: OverlayWindow | None = None,
        panel: GlassPanel | None = None,
    ):
        super().__init__()
        self.settings = settings
        self.adapter = adapter
        self._custom_source = source
        self.region_provider = region_provider or self.capture_region
        self.use_process = use_process
        self.profile: Profile = load_profile(
            settings.profile or demo_profile_dir() / "demo_garden.toml"
        )
        self.lexicon: Lexicon | None = None
        self.solver = None
        self.controller: LiveController | None = None
        self.recognizer: Recognizer | None = None
        self.overlay = overlay if overlay is not None else OverlayWindow()
        self.panel = panel if panel is not None else GlassPanel()
        self.panel.card_clicked.connect(self.select)
        self.panel.pause_clicked.connect(self.toggle_pause)
        self.panel.rescan_clicked.connect(self.rescan)
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
        self.recognizer = self._make_reader(cal)

    def _make_reader(self, cal: Calibration):
        if cal.auto and self.profile.appearance is not None:
            return ViewReader(cal, self.profile)
        return Recognizer(cal, self.profile)

    def set_calibration(self, cal: Calibration) -> None:
        cal.save(self.calibration_path())
        self.settings.calibration = str(self.calibration_path())
        self.recognizer = self._make_reader(cal)
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

    def capture_region(self) -> Region | None:
        """The selected region, or for a profile that finds the board by color, the whole
        display that shows it (the board can then sit anywhere and be zoomed or panned)."""
        if self.profile.appearance is None:
            return self.settings.region_obj
        screens = self.screen_regions()
        if not screens:
            return None
        index = self.settings.board_screen
        return screens[index] if index is not None and 0 <= index < len(screens) else screens[0]

    @staticmethod
    def screen_regions() -> list[Region]:
        """Every display, the main one first."""
        app = QGuiApplication.instance()
        if app is None:
            return []
        screens = [app.primaryScreen()] + [s for s in app.screens() if s != app.primaryScreen()]
        out = []
        for screen in screens:
            if screen is not None:
                g = screen.geometry()
                out.append(Region(g.x(), g.y(), g.width(), g.height()))
        return out

    def find_board_screen(self) -> np.ndarray | None:
        """Capture each display and keep the first one where the board is found."""
        from tilelens.vision.locate import BoardNotFound, locate_board

        if self.profile.appearance is None or self._custom_source is not None:
            return self.source_frame()
        first = None
        for index in range(len(self.screen_regions())):
            self.settings.board_screen = index
            frame = self.source_frame()
            if frame is None:
                continue
            first = frame if first is None else first
            try:
                locate_board(frame, self.profile)
            except BoardNotFound:
                continue
            self.refresh_overlay()
            return frame
        self.settings.board_screen = 0
        return first

    def source_frame(self) -> np.ndarray | None:
        return self._make_source().grab()

    def _make_source(self):
        if self._custom_source is not None:
            src = self._custom_source
        else:
            region = self.capture_region()
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
        if self.adapter is not None:
            # stay over the game, on every Space, also after another app is clicked
            for window in (self.overlay, self.panel):
                window.on_shown = self.adapter.show_on_all_spaces
                self.adapter.show_on_all_spaces(int(window.winId()))
        if self.settings.overlay_mode == "exclude" and self.adapter is not None:
            region = self.region_provider()
            if region:
                self.overlay.place(region)
            self.panel.show()
            self.adapter.show_on_all_spaces(int(self.panel.winId()))
            self.adapter.exclude_from_capture(int(self.panel.winId()))
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
        self.panel.close()
        if self.adapter is not None:
            self.adapter.close()

    # loop

    def _tick(self) -> None:
        if self.controller is None:
            return
        self.controller.tick()
        snap = self.controller.snapshot
        if self.panel.isVisible() and snap.published_at is not None:
            self.panel.set_age(self.controller.clock() - snap.published_at)
        self.timer.start(int(self.controller.interval() * 1000))

    def _on_update(self, snap: Snapshot) -> None:
        if snap.result is None or snap.status != Status.READY:
            self.selected = 0
        self.refresh_overlay(snap)
        self.updated.emit(snap)

    # actions

    def save_debug_dump(self) -> Path | None:
        """Write the latest frame and what was read from it. Only ever called by the user."""
        import json
        import time

        import cv2

        ctrl = self.controller
        if ctrl is None or ctrl.last_frame is None:
            return None
        folder = debug_dump_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stem = folder / time.strftime("dump-%Y%m%d-%H%M%S")
        cv2.imwrite(str(stem.with_suffix(".png")), ctrl.last_frame)
        reading = self.recognizer.read(ctrl.last_frame)
        info = {
            "status": ctrl.snapshot.status.value,
            "detail": ctrl.snapshot.detail,
            "board": [
                "".join(
                    "!"
                    if c.uncertain
                    else "."
                    if not c.letter
                    else (c.letter.lower() if c.blank else c.letter)
                    for c in row
                )
                for row in reading.cells
            ],
            "rack": [t.letter or ("?" if t.blank else "") for t in reading.rack],
            "uncertain": [
                {"row": r, "col": c, "reason": reading.cells[r][c].reason}
                for r, c in reading.uncertain_cells
            ],
            "warnings": reading.warnings,
        }
        stem.with_suffix(".json").write_text(json.dumps(info, indent=2))
        return stem.with_suffix(".png")

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
        self.selection_changed.emit(index)

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

    def overlay_scene(self) -> OverlayScene:
        """Board frame, rack outline and ghost tiles for the newest reading, in overlay points."""
        ctrl, snap = self.controller, self.snapshot
        region = self.region_provider()
        mapper = self.overlay_mapper()
        if ctrl is None or snap is None or region is None or mapper is None:
            return OverlayScene()
        reading = ctrl.last_reading
        if snap.status in (Status.PAUSED, Status.NOT_VISIBLE) or reading is None:
            return OverlayScene()
        view = getattr(reading, "view", None)
        if view is not None and view.visible.any():
            rows, cols = np.nonzero(view.visible)
            r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
        else:
            r0, r1, c0, c1 = 0, self.profile.height - 1, 0, self.profile.width - 1
        board = mapper.rect(int(r0), int(c0), 0).united(mapper.rect(int(r1), int(c1), 0))
        rack = None
        rects = [t.rect for t in reading.rack if t.rect is not None]
        if rects:
            fw, fh = ctrl.last_frame_size
            sx, sy = region.width / fw, region.height / fh
            x0 = min(x for x, _, _, _ in rects)
            y0 = min(y for _, y, _, _ in rects)
            x1 = max(x + w for x, _, w, _ in rects)
            y1 = max(y + h for _, y, _, h in rects)
            rack = QRectF(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy)
        known = sum(1 for t in reading.rack if not t.uncertain and (t.letter or t.blank))
        move = None if self.preview_hidden else self.selected_move()
        return OverlayScene(
            move=move,
            rect_of=mapper.rect,
            board=board,
            coverage=snap.coverage,
            rack=rack,
            rack_read=(known, len(reading.rack)),
            value_of=self.profile.tile_value,
        )

    def refresh_overlay(self, snap: Snapshot | None = None) -> None:
        self.refresh_panel()
        if self.settings.overlay_mode == "panel_only":
            self.overlay.clear()
            self.overlay.hide()
            return
        scene = self.overlay_scene()
        region = self.region_provider()
        if scene.empty or region is None:
            self.overlay.clear()
            self.overlay.hide()
            return
        self.overlay.place(region)
        self.overlay.show_scene(scene)
        if not self.overlay.isVisible():
            self.overlay.show()

    def refresh_panel(self) -> None:
        panel = self.panel
        if panel is None:
            return
        if self.settings.overlay_mode == "panel_only" or self.controller is None:
            panel.hide()
            return
        snap = self.snapshot
        age = None
        if snap is not None and snap.published_at is not None and snap.status == Status.READY:
            age = self.controller.clock() - snap.published_at
        panel.show_snapshot(snap, self.selected, age)
        if not panel.user_moved:
            region = self.region_provider()
            if region is not None:
                panel.move(region.x + region.width - panel.width() - 24, region.y + 72)
        if not panel.isVisible():
            panel.show()


class _NullSource:
    def grab(self):
        return None
