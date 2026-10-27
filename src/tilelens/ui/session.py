from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRectF, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from tilelens.capture.source import PermissionGate, ScreenSource
from tilelens.paths import calibration_dir, debug_dump_dir
from tilelens.platforms import PlatformAdapter, Region
from tilelens.rules.lexicon import Lexicon, WordListError, lexicon_for
from tilelens.rules.profile import Profile, ProfileError, demo_profile_dir, load_profile
from tilelens.state.controller import LiveController, Snapshot, Status
from tilelens.state.worker import InlineSolver, ProcessSolver
from tilelens.ui import notify
from tilelens.ui.design import Appearance
from tilelens.ui.glass import OverlayScene
from tilelens.ui.glass_dialog import GlassDialog
from tilelens.ui.glass_panel import GlassPanel
from tilelens.ui.overlay import OverlayWindow
from tilelens.ui.preview import CellMapper
from tilelens.ui.settings import Settings
from tilelens.vision.autoread import NeedsLetters, ViewReader, calibrate_unaided
from tilelens.vision.recognizer import Calibration, CalibrationError, Recognizer

BACKDROP_EVERY = 1.0
PANEL_SAVE_MS = 600
VISIBLE_PLAYS = 3


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
    settings_changed = Signal(str)

    def __init__(
        self,
        settings: Settings,
        adapter: PlatformAdapter | None = None,
        source=None,
        region_provider: Callable[[], Region | None] | None = None,
        use_process: bool = True,
        overlay: OverlayWindow | None = None,
        panel: GlassPanel | None = None,
        appearance: Appearance | None = None,
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
        self.panel.release_clicked.connect(self.release_hold)
        self.panel.chip_clicked.connect(self.confirm_tile)
        self.panel.moved_by_user.connect(self._panel_moved)
        self.panel.position_reset.connect(self._panel_reset)
        self.panel.compact_changed.connect(self._compact_changed)
        self.panel.set_compact(settings.panel_compact)
        self.panel.set_shortcuts(settings.shortcuts)
        self.preview_hidden = False
        self._appearance: Appearance | None = None
        self._sampled_at = 0.0
        self._outline: tuple[QRectF | None, QRectF | None] | None = None
        self._capture_mode: str | None = None
        self._offset_timer = QTimer(self)
        self._offset_timer.setSingleShot(True)
        self._offset_timer.setInterval(PANEL_SAVE_MS)
        self._offset_timer.timeout.connect(lambda: self.settings_changed.emit("panel_offset"))
        self._grid_key = None
        self._saved_at = 0.0
        self._words_seen = None
        self.needs_letters = None
        self._saved_mark = None
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
        if appearance is not None:
            self.appearance = appearance

    # appearance

    @property
    def appearance(self) -> Appearance | None:
        return self._appearance

    @appearance.setter
    def appearance(self, value: Appearance | None) -> None:
        self._appearance = value
        if value is not None:
            self.panel.bind_appearance(value)
            self.overlay.bind(value)

    def _sample_backdrop(self) -> None:
        """Tell the appearance how bright and busy the game is behind the panel, once a second."""
        appearance, ctrl = self._appearance, self.controller
        if appearance is None or ctrl is None or ctrl.last_frame is None:
            return
        now = time.monotonic()
        if now - self._sampled_at < BACKDROP_EVERY:
            return
        self._sampled_at = now
        region = self.region_provider()
        if region is None or region.width <= 0 or region.height <= 0 or not self.panel.isVisible():
            return
        frame = ctrl.last_frame
        fh, fw = frame.shape[:2]
        sx, sy = fw / region.width, fh / region.height
        glass = self.panel.glass_geometry()
        x0, y0 = (glass.left() - region.x) * sx, (glass.top() - region.y) * sy
        x1, y1 = x0 + glass.width() * sx, y0 + glass.height() * sy
        seen = max(0.0, min(x1, fw) - max(x0, 0.0)) * max(0.0, min(y1, fh) - max(y0, 0.0))
        if seen < 0.25 * max(1.0, (x1 - x0) * (y1 - y0)):
            return
        appearance.sample(frame, (round(x0), round(y0), round(x1 - x0), round(y1 - y0)))

    # windows

    def _exclusion_wanted(self) -> bool:
        return self.settings.overlay_mode == "exclude" and not os.environ.get(
            "TILELENS_SCREENSHOT_MODE"
        )

    def _shown(self, win_id: int) -> None:
        """Every window of ours, each time it appears: over the game on every Space and, when
        the overlay mode says so, out of the screen capture. Runs before the blur is asked."""
        adapter = self.adapter
        if adapter is None:
            return
        adapter.show_on_all_spaces(win_id)
        if self._exclusion_wanted():
            adapter.exclude_from_capture(win_id)

    def _install_window_hooks(self) -> None:
        for window in (self.overlay, self.panel):
            window.on_shown = self._shown
        notify.manager().on_shown = self._shown
        GlassDialog.on_shown = self._shown

    def _apply_capture_mode(self) -> None:
        mode = self.settings.overlay_mode
        if mode == self._capture_mode:
            return
        self._capture_mode = mode
        if self.adapter is not None and self._exclusion_wanted():
            for window in (self.overlay, self.panel):
                if window.isVisible():
                    self.adapter.exclude_from_capture(int(window.winId()))

    def toast(self, text: str, kind: str = "success", ms: int = 2500) -> None:
        """A confirmation that leaves by itself, under the panel so the board stays clear."""
        anchor = ("below", self.panel.glass_geometry()) if self.panel.isVisible() else None
        notify.show(text, kind, ms, anchor)

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

    def auto_calibrate(self) -> bool:
        """Learn the game's look from the screen as it is, reading the rack without typing.

        Returns True when tracking can start. When tiles need a human, needs_letters holds
        the best guess and which tiles are unsure.
        """
        self.needs_letters = None
        if self.profile.appearance is None or self.adapter is None:
            return False
        frame = self.find_board_screen()
        if frame is None:
            self.note("Nothing captured. Check the Screen Recording permission.")
            return False
        previous = self.recognizer.cal if self.recognizer is not None else None
        try:
            cal = calibrate_unaided(frame, self.profile, previous)
        except NeedsLetters as exc:
            self.needs_letters = exc
            tiles = ", ".join(str(i + 1) for i in exc.unsure)
            self.note(f"Confirm rack tile {tiles}: open Calibrate and check the letters")
            return False
        except CalibrationError as exc:
            self.note(str(exc))
            return False
        self.set_calibration(cal)
        self.note("")
        return True

    def setup_state(self) -> tuple[bool | None, bool | None, bool | None]:
        """Screen capture allowed, word list loaded, game learned: True done, False a problem,
        None still to do."""
        permission = self.adapter.screen_permission() if self.adapter is not None else True
        return (
            None if permission is None else bool(permission),
            self.lexicon is not None,
            True if self.recognizer is not None else None,
        )

    def show_setup_panel(self) -> None:
        self._install_window_hooks()
        self.panel.show_snapshot(None, 0)
        self.panel.set_setup(*self.setup_state())
        self._place_panel()
        self.panel.show()
        self._shown(int(self.panel.winId()))

    def note(self, text: str) -> None:
        self.panel.set_note(text)
        self.message.emit(text)

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
            lexicon=self.lexicon,
        )
        self.controller.on_misaligned = self._clear_preview
        self.controller.on_event = self._log_event
        self._install_window_hooks()
        if self.adapter is not None:
            # stay over the game, on every Space, also after another app is clicked
            for window in (self.overlay, self.panel):
                self.adapter.show_on_all_spaces(int(window.winId()))
        if self.settings.overlay_mode == "exclude" and self.adapter is not None:
            region = self.region_provider()
            if region:
                self.overlay.place(region)
            self._place_panel()
            self.panel.show()
            if not os.environ.get("TILELENS_SCREENSHOT_MODE") and (
                not self.overlay.try_exclude_from_capture(self.adapter)
            ):
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
        self._outline = None
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
        self._offset_timer.stop()
        manager = notify.manager()
        if getattr(manager.on_shown, "__self__", None) is self:
            manager.on_shown = None
        if getattr(GlassDialog.on_shown, "__self__", None) is self:
            GlassDialog.on_shown = None
        self.overlay.close()
        self.panel.close()
        if self.adapter is not None:
            self.adapter.close()

    # loop

    def _tick(self) -> None:
        if self.controller is None:
            return
        self.controller.tick()
        self._sample_backdrop()
        self._check_values()
        self._check_words()
        self._save_learning()
        snap = self.controller.snapshot
        grid = self.controller.last_grid
        key = None if grid is None else (round(grid.x0), round(grid.y0), round(grid.pitch_x))
        if key != self._grid_key:
            # the board moved or was lost: redraw (or clear) the preview at once
            self._grid_key = key
            self.refresh_overlay()
        if self.panel.isVisible() and snap.published_at is not None:
            self.panel.set_age(self.controller.clock() - snap.published_at)
        self.timer.start(int(self.controller.interval() * 1000))

    def adopt_values(self, learned: dict[str, int]) -> None:
        """Use tile values read from the game itself: save them beside the profile and
        restart the search so every score follows them."""
        from tilelens.rules.profile import learned_values, save_learned_values

        path = self.profile.source
        if path is None or self.controller is None:
            return
        merged = learned_values(path)
        merged.update(learned)
        save_learned_values(path, merged)
        self.profile.letter_values.update(learned)
        reader = self.recognizer
        if hasattr(reader, "known_values"):
            reader.known_values.update(learned)
        self._restart_solver()
        ctrl = self.controller
        ctrl.solver = self.solver
        ctrl.cache.clear()
        if ctrl.hold is None:
            ctrl.rescan()
        text = ", ".join(f"{k}={v}" for k, v in sorted(learned.items()))
        self.toast(f"Learned tile values from the game: {text}")

    def _save_learning(self) -> None:
        """Keep what was learned from play (letter shapes, tile colors) across restarts."""
        now = time.monotonic()
        if now - self._saved_at < 5.0:
            return
        self._saved_at = now
        cal = getattr(self.recognizer, "cal", None)
        if cal is None:
            return
        mark = (
            sum(len(v) for v in cal.templates.values()),
            sum(len(v) for v in cal.rack_templates.values()),
            len(cal.tile_backgrounds),
            sum(len(v) for v in cal.empty_glyphs.values()),
        )
        if mark != self._saved_mark:
            if self._saved_mark is not None:
                with contextlib.suppress(OSError):
                    cal.save(self.calibration_path())
            self._saved_mark = mark

    def _restart_solver(self) -> None:
        if self.solver is not None:
            self.solver.close()
        self.solver = None
        if self.use_process and self.profile.source is not None:
            self.solver = ProcessSolver(self.profile.source, self.settings.words)
        else:
            from tilelens.solver.movegen import MoveGenerator

            self.solver = InlineSolver(MoveGenerator(self.profile, self.lexicon, use_cache=True))

    def _words_stamp(self) -> tuple:
        from tilelens.rules.overrides import default_path

        out = []
        for path in (
            default_path(self.profile.source),
            self.settings.words or self.profile.word_list,
        ):
            try:
                st = Path(path).stat()
                out.append((st.st_mtime_ns, st.st_size))
            except (OSError, TypeError):
                out.append(None)
        return tuple(out)

    def _check_words(self) -> None:
        """Pick up a word recorded with `tilelens words allow|block` while TileLens runs."""
        stamp = self._words_stamp()
        if stamp == self._words_seen:
            return
        self._words_seen = stamp
        before = self.lexicon.version if self.lexicon is not None else None
        self._load_words()
        if self.lexicon is None or self.controller is None or self.lexicon.version == before:
            return
        self._restart_solver()
        ctrl = self.controller
        ctrl.solver, ctrl.lexicon, ctrl.lexicon_version = (
            self.solver,
            self.lexicon,
            self.lexicon.version,
        )
        ctrl.cache.clear()  # results made under the old words are never shown again
        ctrl.rescan()
        ctrl.event(f"words changed: now {self.lexicon.version}")
        self.toast("Word list changed. Suggestions refreshed.")

    def _check_values(self) -> None:
        reader = self.recognizer
        values = getattr(reader, "values", None)
        if values is None:
            return
        known = getattr(reader, "known_values", {})
        learned = {k: v for k, v in values.trusted().items() if known.get(k) != v}
        if learned:
            self.adopt_values(learned)

    def _on_update(self, snap: Snapshot) -> None:
        if (snap.result is None or snap.status != Status.READY) and snap.held is None:
            self.selected = 0
        if snap.held is not None:
            self.selected = snap.held
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

    def select(self, index: int, hold: bool = True) -> None:
        """Clicking a card shows that play on the board and holds it still while the player
        places the tiles; clicking the held card again lets go."""
        ctrl = self.controller
        if ctrl is not None:
            if not self._playable(index):
                return
            if ctrl.hold is not None and ctrl.hold.index == index and self.selected == index:
                ctrl.release_hold()
                self.refresh_overlay()
                self.selection_changed.emit(index)
                return
            self.selected = index
            if hold or ctrl.hold is not None:
                ctrl.hold_move(index)
        else:
            self.selected = index
        self.refresh_overlay()
        self.selection_changed.emit(index)

    def _playable(self, index: int) -> bool:
        """A shortcut or click can name a play that is not on show; that changes nothing."""
        snap = self.snapshot
        if snap is None or snap.result is None or snap.status != Status.READY:
            return True
        return 0 <= index < min(VISIBLE_PLAYS, len(snap.result.moves))

    def release_hold(self) -> None:
        if self.controller is not None:
            self.controller.release_hold()
        self.refresh_overlay()

    def confirm_tile(self, kind: str, where, letter: str) -> None:
        if self.controller is not None:
            self.controller.confirm_tile(kind, where, letter)

    def next_card(self) -> None:
        snap = self.snapshot
        if snap and snap.result and snap.result.moves:
            shown = min(VISIBLE_PLAYS, len(snap.result.moves))
            self.select((self.selected + 1) % shown, hold=False)

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

    def board_framed(self) -> bool:
        """The whole board is inside the game's canvas; any unseen squares are under its panels."""
        reading = self.controller.last_reading if self.controller is not None else None
        view = getattr(reading, "view", None)
        return view is not None and view.full

    def _outlines(
        self, mapper: CellMapper, reading, region: Region
    ) -> tuple[QRectF, QRectF | None]:
        """The visible board and the rack, in overlay points."""
        ctrl = self.controller
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
        return board, rack

    def _asking(self, snap: Snapshot, mapper: CellMapper, reading, region: Region):
        """The one tile the panel asks about, as a rectangle; nothing while a play is held."""
        if snap.held is not None or not snap.unsure or snap.result is None:
            return ()
        kind, where = snap.unsure[0][0], snap.unsure[0][1]
        if kind == "cell":
            return (mapper.rect(where[0], where[1], 0.03),)
        if kind == "rack" and 0 <= where < len(reading.rack) and reading.rack[where].rect:
            fw, fh = self.controller.last_frame_size
            x, y, w, h = reading.rack[where].rect
            sx, sy = region.width / fw, region.height / fh
            return (QRectF(x * sx, y * sy, w * sx, h * sy),)
        return ()

    @staticmethod
    def _occupied(reading) -> Callable[[int, int], bool]:
        cells = reading.cells
        rows = len(cells)
        cols = len(cells[0]) if rows else 0

        def occupied(row: int, col: int) -> bool:
            if not (0 <= row < rows and 0 <= col < cols):
                return False
            cell = cells[row][col]
            return bool(cell.letter or cell.blank or cell.uncertain)

        return occupied

    def overlay_scene(self) -> OverlayScene:
        """The suggested play, the guides and the rack outline for the newest reading, in
        overlay points. Nothing here marks tiles that were read."""
        ctrl, snap = self.controller, self.snapshot
        region = self.region_provider()
        mapper = self.overlay_mapper()
        if ctrl is None or snap is None or region is None or mapper is None:
            return OverlayScene()
        reading = ctrl.last_reading
        if snap.status in (Status.PAUSED, Status.NOT_VISIBLE) or reading is None:
            return OverlayScene()
        board, rack = self._outlines(mapper, reading, region)
        known = sum(1 for t in reading.rack if not t.uncertain and (t.letter or t.blank))
        move = None if self.preview_hidden else self.selected_move()
        total = None
        if move is not None and snap.result is not None:
            total = min(VISIBLE_PLAYS, len(snap.result.moves))
        steps = (round(v / 8) for v in (board.x(), board.y(), board.width(), board.height()))
        lock = (region.width, region.height, *steps)
        return OverlayScene(
            move=move,
            rect_of=mapper.rect,
            board=board,
            coverage=snap.coverage,
            rack=rack,
            rack_read=(known, len(reading.rack)),
            value_of=self.profile.tile_value,
            framed=self.board_framed(),
            rank=None if move is None else self.selected + 1,
            total=total,
            asking=self._asking(snap, mapper, reading, region),
            guides_mode=self.settings.guides,
            lock=lock,
            occupied=self._occupied(reading),
        )

    def _log_event(self, text: str) -> None:
        """Append what the tracker decided to a small log, for finding out why it did it."""
        from tilelens.paths import data_dir

        path = data_dir() / "events.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 200_000:
                path.write_text(path.read_text()[-50_000:])
            with path.open("a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _clear_preview(self) -> None:
        self._grid_key = None
        self.overlay.clear()
        self.overlay.hide()

    def refresh_overlay(self, snap: Snapshot | None = None) -> None:
        self.refresh_panel()
        self._apply_capture_mode()
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
        panel.set_compact(self.settings.panel_compact)
        snap = self.snapshot
        age = None
        if snap is not None and snap.published_at is not None and snap.status == Status.READY:
            age = self.controller.clock() - snap.published_at
        panel.show_snapshot(snap, self.selected, age, self.board_framed())
        if snap is None:
            panel.set_setup(*self.setup_state())
        self._place_panel()
        if not panel.isVisible():
            panel.show()
            self._place_panel()

    def _placement_rects(self, region: Region) -> tuple[QRectF | None, QRectF | None]:
        """Board and rack in screen points. They stay at the last place they were seen while
        the game is paused or out of view, so the panel does not jump."""
        ctrl = self.controller
        mapper = self.overlay_mapper()
        reading = ctrl.last_reading if ctrl is not None else None
        if mapper is not None and reading is not None:
            self._outline = self._outlines(mapper, reading, region)
        board, rack = self._outline or (None, None)
        return (
            None if board is None else board.translated(region.x, region.y),
            None if rack is None else rack.translated(region.x, region.y),
        )

    def _place_panel(self) -> None:
        """Beside the board, or where the player put it (stored relative to the region)."""
        region = self.region_provider()
        if region is None:
            return
        offset = self.settings.panel_offset
        if offset is not None:
            want = QPoint(region.x + offset["x"], region.y + offset["y"])
            if self.panel.user_position != want:
                self.panel.set_user_position(want)
        board, rack = self._placement_rects(region)
        self.panel.auto_place(region, board, rack)

    def _panel_moved(self, glass_top_left: QPoint) -> None:
        region = self.region_provider()
        if region is None:
            return
        self.settings.panel_offset = {
            "x": round(glass_top_left.x() - region.x),
            "y": round(glass_top_left.y() - region.y),
        }
        self._offset_timer.start()

    def _panel_reset(self) -> None:
        self.settings.panel_offset = None
        self._offset_timer.stop()
        self.settings_changed.emit("panel_offset")
        self.refresh_panel()

    def _compact_changed(self, compact: bool) -> None:
        if self.settings.panel_compact == compact:
            return
        self.settings.panel_compact = compact
        self.settings_changed.emit("panel_compact")
        self.refresh_panel()


class _NullSource:
    def grab(self):
        return None
