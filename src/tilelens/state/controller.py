from __future__ import annotations

import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

import numpy as np

from tilelens.capture.change import ChangeDetector
from tilelens.rules.board import WALL_CELL, Board, rack_to_str
from tilelens.rules.profile import BLANK, Profile
from tilelens.solver.movegen import SearchResult
from tilelens.state.memory import BoardMemory, Coverage
from tilelens.state.names import square_name
from tilelens.state.position import position_id
from tilelens.vision.lexfix import drop_isolated, settle
from tilelens.vision.recognizer import (
    COMMITTED,
    RECENT,
    SCALE_CHANGED,
    TENTATIVE,
    UNSEEN,
    CellFix,
    CellRead,
    Reading,
    Recognizer,
    as_fix,
)

MIN_HZ, MAX_HZ = 8.0, 12.0
IDLE_AFTER = 2.0
IDLE_INTERVAL = 0.5
CACHE_SIZE = 64
REMEMBERED_MIN = 0.8
ASSUMED_SHOWN = 8.0
SOFT_AFTER = 0.6
HELD_UNSEEN = 4.0


class Status(StrEnum):
    READING = "Reading"
    SOLVING = "Solving"
    READY = "Ready"
    CHECK = "Check tiles"
    PAUSED = "Paused"
    NOT_VISIBLE = "Game not visible"


WAITING = "Waiting for board"
HELD = "Holding this suggestion"


@dataclass(frozen=True)
class Snapshot:
    status: Status
    detail: str = ""
    pid: str | None = None
    board: Board | None = None
    rack: Counter | None = None
    result: SearchResult | None = None
    reading: Reading | None = field(default=None, compare=False)
    confirmed_at: float | None = None
    published_at: float | None = None
    solve_ms: float | None = None
    coverage: Coverage | None = None
    notice: str = ""
    unsure: tuple = ()
    held: int | None = None

    @property
    def showing_result(self) -> bool:
        return self.result is not None


@dataclass
class HeldPlay:
    """A suggestion the player chose to place: everything is frozen until the rack refills."""

    index: int
    rack_size: int
    seen_short: bool = False
    short_frames: int = 0
    full_frames: int = 0
    last_state: str = ""
    new_letters: bool = False
    unseen_since: float | None = None


class LiveController:
    """Turns captured frames into at most one current, never-stale search result.

    Pure logic with injected source, recognizer, solver and clock so replays can drive it.
    """

    def __init__(
        self,
        source,
        recognizer: Recognizer,
        solver,
        profile: Profile,
        lexicon_version: str,
        clock: Callable[[], float] = time.monotonic,
        hz: float = 10.0,
        stable_frames: int = 3,
        on_update: Callable[[Snapshot], None] | None = None,
        lexicon=None,
        soft: bool = True,
    ):
        self.lexicon = lexicon
        self.soft = soft
        self.on_misaligned: Callable[[], None] | None = None
        self.on_event: Callable[[str], None] | None = None
        self.events: list[tuple[float, str]] = []
        self.source = source
        self.recognizer = recognizer
        self.solver = solver
        self.profile = profile
        self.lexicon_version = lexicon_version
        self.clock = clock
        self.hz = min(MAX_HZ, max(MIN_HZ, hz))
        self.stable_frames = max(1, stable_frames)
        self.on_update = on_update
        self.cache: OrderedDict[str, SearchResult] = OrderedDict()
        self.paused = False
        self.stale_discarded = 0
        self.searches_started = 0
        self._change: ChangeDetector | None = None
        self._snapshot = Snapshot(Status.READING, "Starting")
        self._candidate_sig = None
        self._count = 0
        self._stable_sig = None
        self._stable_frame: np.ndarray | None = None
        self._check_reading: Reading | None = None
        self._last_change = clock()
        self._force = False
        self._confirmed_rack_size: int | None = None
        self._submitted_at: float | None = None
        self._confirmed_at: float | None = None
        self.last_confirmed: tuple[Board, Counter, list[str]] | None = None
        self.last_grid = None
        self.last_frame: np.ndarray | None = None
        self.last_frame_size: tuple[int, int] | None = None
        self.last_read_ms: float | None = None
        self.memory = BoardMemory()
        self._refining: tuple[str, dict] | None = None
        self.last_reading: Reading | None = None
        self._aligned_at = -1e9
        self._rack_tiles = 0
        self._star_logged = False
        self._soft_sig = None
        self._soft_since = 0.0
        self._assumed: tuple[str, float] | None = None
        self.notice = ""
        self._walled: list = []
        self.unsure: tuple = ()
        self.hold: HeldPlay | None = None
        self._unsure_reading: Reading | None = None

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    def _set(self, status: Status, detail: str = "", **kw) -> None:
        kw.setdefault("notice", self.notice)
        kw.setdefault("unsure", self.unsure)
        if self.hold is not None:
            kw.setdefault("held", self.hold.index)
        snap = Snapshot(status, detail, **kw)
        if snap != self._snapshot:
            self._snapshot = snap
            if self.on_update:
                self.on_update(snap)

    def interval(self) -> float:
        if self.paused:
            return IDLE_INTERVAL
        quiet = self.clock() - self._last_change > IDLE_AFTER
        if quiet and self._snapshot.status in (Status.READY, Status.CHECK, Status.NOT_VISIBLE):
            return IDLE_INTERVAL
        return 1.0 / self.hz

    def pause(self) -> None:
        if self.hold is not None:
            self.event("hold released: paused")
        self.hold = None
        self.paused = True
        self.solver.cancel()
        self._reset_stability()
        self._set(Status.PAUSED, "Paused")

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self._set(Status.READING, "Reading")

    def rescan(self) -> None:
        if self.hold is not None:
            self.event("hold released: rescan")
        self.hold = None
        self._reset_stability()
        self._force = True
        self._set(Status.READING, "Reading")

    def set_recognizer(self, recognizer: Recognizer) -> None:
        self.recognizer = recognizer
        self._change = None
        self.rescan()

    def _reset_stability(self) -> None:
        self._candidate_sig = None
        self._count = 0
        self._stable_sig = None
        self._check_reading = None
        if self._change:
            self._change.reset()

    def _watch_regions(self, frame) -> ChangeDetector:
        if self._change is None:
            cal = self.recognizer.cal
            h, w = frame.shape[:2]
            if getattr(self.recognizer, "tracks_view", False):
                # the board moves with zoom and pan, so any pixel may matter
                self._change = ChangeDetector([(0, 0, w, h)])
                return self._change
            cw, ch = cal.image_size
            fx, fy = w / cw, h / ch
            gx, gy, gw, gh = cal.grid.rect
            x0, y0, x1, y1 = cal.rack_band

            def sc(r):
                return (round(r[0] * fx), round(r[1] * fy), round(r[2] * fx), round(r[3] * fy))

            regions = [sc((gx, gy, gw, gh)), sc((x0, y0, x1 - x0, y1 - y0))]
            self._change = ChangeDetector(regions, [sc(m) for m in cal.masks])
        return self._change

    def tick(self) -> None:
        """Capture and read first, then accept search results, so a result is only published
        if the newest frame still shows the position it was computed for."""
        now = self.clock()
        self._observe(now)
        self._drain(self.clock())

    def _observe(self, now: float) -> None:
        if self.paused:
            self._set(Status.PAUSED, "Paused")
            return
        frame = self.source.grab()
        self.last_frame = frame
        if self.hold is not None:
            self._observe_held(frame, now)
            return
        if frame is None:
            self._not_visible(getattr(self.source, "last_error", None) or "Capture failed")
            return
        detector = self._watch_regions(frame)
        changed = detector.update(frame)
        if changed:
            self._last_change = now
        snap = self._snapshot
        settled = (
            self._stable_sig is not None
            and self._candidate_sig == self._stable_sig
            and (snap.status != Status.READING or snap.detail == WAITING)
        )
        if not changed and settled and not self._force:
            return
        locates = getattr(self.recognizer, "locates", 0)
        aligned = getattr(self.recognizer, "is_aligned", None)
        if aligned is not None and not aligned(frame):
            # the board moved (zoom, pan, resize): drop the preview before finding it again
            self.last_grid = None
            if self.on_misaligned:
                self.on_misaligned()
        reading = self.recognizer.read(frame)
        if getattr(self.recognizer, "locates", 0) != locates:
            self._aligned_at = now
        self.last_reading = reading
        self.last_grid = reading.grid
        self.last_frame_size = (frame.shape[1], frame.shape[0])
        self.last_read_ms = reading.elapsed * 1000
        if getattr(reading, "star_seen", False) != self._star_logged:
            self._star_logged = bool(getattr(reading, "star_seen", False))
            self.event(
                "starting round: the star is on the start square"
                if self._star_logged
                else "starting round over: the start square is covered"
            )
        if not reading.visible:
            self._not_visible("Board not found in the captured region")
            return
        self._confirm_remembered(reading)
        sig = reading.signature()
        if sig == self._candidate_sig:
            self._count += 1
        else:
            self._candidate_sig = sig
            self._count = 1
        if sig == self._stable_sig and not self._force and self._snapshot.status != Status.READING:
            return
        if self._count >= self.stable_frames:
            self._force = False
            self._on_stable(reading, frame, sig, now)
        elif self._showing() and self._consistent(reading):
            pass  # zooming, panning or placing: the shown result still holds
        else:
            detail = WAITING if reading.tentative_squares() else "Reading"
            if detail == "Reading" and now - self._aligned_at < 1.0:
                detail = "Aligning"
            self._set(Status.READING, detail)

    def hold_move(self, index: int) -> bool:
        """Freeze the shown suggestion while the player places its tiles."""
        snap = self._snapshot
        if (
            snap.status != Status.READY
            or snap.result is None
            or not 0 <= index < len(snap.result.moves)
        ):
            return False
        size = self._confirmed_rack_size or self.profile.rack_size
        if self.hold is None:
            self.hold = HeldPlay(index, size)
            self.event(f"hold started on card {index + 1}")
        else:
            self.hold.index = index
        self.solver.cancel()
        self._refining = None
        self._set(Status.READY, HELD, result=snap.result, published_at=snap.published_at,
                  solve_ms=snap.solve_ms, **self._keep(snap))  # fmt: skip
        return True

    def release_hold(self, reason: str = "released") -> None:
        """Back to live tracking: read the board and rack afresh."""
        if self.hold is None:
            return
        self.hold = None
        self.event(f"hold released: {reason}")
        self._reset_stability()
        self._force = True
        self._set(Status.READING, "Reading")

    def event(self, text: str) -> None:
        self.events.append((self.clock(), text))
        del self.events[:-200]
        if self.on_event:
            self.on_event(text)

    def _observe_held(self, frame, now: float) -> None:
        """While held only the board's position is followed, so the ghost tiles stay put.

        The hold ends only when the rack was seen short (tiles were placed) and is then full
        again with different letters for several frames in a row. A flicker, a hover, a dragged
        tile hiding the board or a recalled tile never ends it.
        """
        hold = self.hold
        if frame is None:
            self._held_unseen(now)
            return
        changed = self._watch_regions(frame).update(frame)
        if changed:
            self._last_change = now
        if not (changed or self._force):
            # a still screen keeps confirming what the last frame showed
            if hold.last_state == "full" and hold.new_letters:
                hold.full_frames += 1
                self._maybe_release()
            return
        self._force = False
        reading = self.recognizer.read(frame)
        self.last_reading = reading
        if not reading.visible:
            self._held_unseen(now)
            return
        hold.unseen_since = None
        self.last_grid = reading.grid
        self.last_frame_size = (frame.shape[1], frame.shape[0])
        if len(reading.rack) < hold.rack_size:
            hold.last_state = "short"
            hold.short_frames += 1
            hold.full_frames = 0
            hold.new_letters = False
            if not hold.seen_short and hold.short_frames >= self.stable_frames:
                hold.seen_short = True
                self.event("hold: tiles are being placed")
            return
        hold.short_frames = 0
        hold.last_state = "full"
        held_rack = self._snapshot.rack or Counter()
        clear = Counter(t.letter for t in reading.rack if t.letter and not t.uncertain)
        # a tile that is not in the held rack, or one too unclear to say, is a new letter
        same = bool(clear - held_rack) or reading.rack_uncertain
        if same != hold.new_letters:
            hold.full_frames = 0
        hold.new_letters = same
        if same:
            hold.full_frames += 1
        self._maybe_release()

    def _held_unseen(self, now: float) -> None:
        """The board is hidden for a moment (a hand over it): keep the last alignment."""
        hold = self.hold
        if hold.unseen_since is None:
            hold.unseen_since = now
        if now - hold.unseen_since > HELD_UNSEEN:
            self.last_grid = None

    def _maybe_release(self) -> None:
        hold = self.hold
        if hold.seen_short and hold.new_letters and hold.full_frames >= self.stable_frames:
            self.release_hold("rack refilled with new letters")

    def _not_visible(self, why: str) -> None:
        self.solver.cancel()
        self._reset_stability()
        self._set(Status.NOT_VISIBLE, why)

    def _on_stable(self, reading: Reading, frame, sig, now) -> None:
        self._stable_sig = sig
        self._stable_frame = frame
        self._check_reading = None
        rack = reading.rack_counter()
        if self._placing(reading, rack):
            self._wait_for_submit()
            return
        if reading.tentative_squares():
            shrunk = (
                self._confirmed_rack_size is not None
                and sum(rack.values()) < self._confirmed_rack_size
            )
            if shrunk or not any(
                reading.cells[r][c].uncertain for r, c in reading.tentative_squares()
            ):
                self._wait_for_submit()
                return
        self._confirm_remembered(reading, frame)
        if self.soft:
            drop_isolated(reading)
        fixed = settle(reading, self.lexicon) if self.lexicon is not None else None
        if not reading.confident:
            if not self.soft:
                self._hard_check(reading)
                return
            if self._soft_sig != sig:
                self._soft_sig, self._soft_since = sig, now
            if now - self._soft_since < SOFT_AFTER:
                # an animation or a hover can look unclear for a moment; give it time to settle
                self._set(Status.READING, "Reading")
                return
            soft = self._soften(reading, fixed)
            if soft is None:
                self._hard_check(reading)
                return
            rack = soft
        self._notes(reading, fixed)
        order = [BLANK if t.blank else (t.letter or "") for t in reading.rack]
        consolidate = getattr(self.recognizer, "consolidate", None)
        if consolidate is not None and reading.confident:
            consolidate(reading)
        self._rack_tiles = len(reading.rack)
        remember = getattr(self.recognizer, "remember_rack", None)
        if remember is not None and not reading.rack_uncertain:
            remember(reading)
        self._confirm(reading.placed(), rack, order, now)

    def _hard_check(self, reading: Reading) -> None:
        self._check_reading = reading
        self.notice, self.unsure = "", ()
        self.solver.cancel()
        self._set(Status.CHECK, self._check_detail(reading), reading=reading)

    def _soften(self, reading: Reading, fixed) -> Counter | None:
        """Carry on with what is clear: unclear squares become walls, an unclear rack tile
        keeps its best guess. None when something cannot be worked around."""
        if reading.warnings or any(t.letter is None and not t.blank for t in reading.rack):
            return None
        self._unsure_reading = reading
        asked = []
        for r, c in reading.uncertain_cells:
            cell = reading.cells[r][c]
            if self._touches_tile(reading, r, c):
                asked.append((r, c, cell.letter, cell.candidates))
        self._walled = asked
        for r, c in reading.uncertain_cells:
            reading.cells[r][c] = CellRead(state=UNSEEN)
        # a guess at an unclear tile could make a suggestion nobody can play, so searches use
        # only the tiles that are certain; the panel asks about the other
        return Counter(
            BLANK if t.blank else t.letter
            for t in reading.rack
            if (t.blank or t.letter) and not t.uncertain
        )

    @staticmethod
    def _touches_tile(reading: Reading, r: int, c: int) -> bool:
        """A new tile always joins the others, so an unclear square with no tile beside it is
        part of the game's interface, not a tile worth asking about."""
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < len(reading.cells) and 0 <= cc < len(reading.cells[0]):
                near = reading.cells[rr][cc]
                if near.letter and near.state in (COMMITTED, RECENT):
                    return True
        return False

    def _notes(self, reading: Reading, fixed) -> None:
        """What the player should know, without stopping the suggestions."""
        unsure = [
            ("rack", i, t.letter, t.candidates) for i, t in enumerate(reading.rack) if t.uncertain
        ]
        unsure += [("cell", (r, c), letter, cands) for r, c, letter, cands in self._walled]
        self._walled = []
        notice = ""
        if unsure:
            kind, where, guess, _ = unsure[0]
            name = f"rack tile {where + 1}" if kind == "rack" else f"square {square_name(*where)}"
            more = f" (+{len(unsure) - 1} more)" if len(unsure) > 1 else ""
            guess_text = f"looks like {guess}" if guess else "is unclear"
            notice = (
                f"{name[0].upper()}{name[1:]} {guess_text}; "
                f"plays leave it out until you tap its letter{more}"
            )
        elif fixed is not None and any(was != now for _, _, was, now in fixed.changed):
            r, c, was, now = next(x for x in fixed.changed if x[2] != x[3])
            notice = f"Read {was or '?'} at {square_name(r, c)} as {now}: only {now} makes a word"
            self._assumed = (notice, self.clock())
        elif self._assumed is not None and self.clock() - self._assumed[1] < ASSUMED_SHOWN:
            notice = self._assumed[0]
        self.notice, self.unsure = notice, tuple(unsure)

    def confirm_tile(self, kind: str, where, letter: str) -> bool:
        """The player named an unclear tile: learn it, then read again."""
        reading, frame = self._unsure_reading, self._stable_frame
        if reading is None or frame is None:
            return False
        if kind == "rack":
            self.recognizer.learn_from_corrections(frame, reading, {}, {where: letter})
        else:
            self.recognizer.learn_from_corrections(frame, reading, {where: (letter, False)})
        self.rescan()
        return True

    def _confirm_remembered(self, reading: Reading, frame=None) -> None:
        """Played tiles never change, so a remembered tile settles a read of the same square.

        That covers an unclear read, and a confident one whose runner-up letters include the
        remembered one (a lookalike at a new zoom). With a frame, the glyph is learned as the
        remembered letter, which teaches it at the current zoom.
        """
        mem = self.memory.board
        if mem is None or (mem.width, mem.height) != (len(reading.cells[0]), len(reading.cells)):
            return
        learn = {}
        for r, row in enumerate(reading.cells):
            for c, cell in enumerate(row):
                known = mem.cells[r][c]
                if mem.is_unknown(r, c) or known is None or cell.letter is None:
                    continue
                if cell.state == TENTATIVE:
                    continue
                letter, blank = known
                if not cell.uncertain and (cell.letter, cell.blank) == known:
                    continue
                if not cell.uncertain and letter not in cell.candidates:
                    continue  # a clearly different tile: let the change be seen
                if cell.letter != letter or cell.letter_score >= REMEMBERED_MIN:
                    learn[(r, c)] = (letter, blank)
                state = cell.state if cell.state in (COMMITTED, RECENT) else COMMITTED
                reading.cells[r][c] = replace(
                    cell,
                    letter=letter,
                    blank=blank,
                    state=state,
                    uncertain=False,
                    reason="",
                    value=cell.value if cell.letter == letter else None,
                )
        if learn and frame is not None:
            self.recognizer.learn_from_corrections(frame, reading, learn)

    def _tiles_removed(self, board: Board) -> bool:
        mem = self.memory.board
        if mem is None or (mem.width, mem.height) != (board.width, board.height):
            return False
        return any(
            mem.cells[r][c] is not None
            and not mem.is_unknown(r, c)
            and not board.is_unknown(r, c)
            and board.cells[r][c] is None
            for r in range(board.height)
            for c in range(board.width)
        )

    def _showing(self) -> bool:
        snap = self._snapshot
        return snap.status == Status.READY and snap.result is not None and snap.board is not None

    def _wait_for_submit(self) -> None:
        """Tiles are being placed: keep the suggestion in view until they are submitted."""
        self.solver.cancel()
        self._refining = None
        if self._showing():
            snap = self._snapshot
            self._set(
                Status.READY,
                WAITING,
                result=snap.result,
                published_at=snap.published_at,
                solve_ms=snap.solve_ms,
                **self._keep(snap),
            )
        else:
            self._set(Status.READING, WAITING)

    def _consistent(self, reading: Reading) -> bool:
        """Nothing in view contradicts the shown position.

        Squares that were unknown, unclear squares and tiles the player is placing from the
        rack do not count as contradictions; a tile that appeared otherwise, or one that
        changed or vanished, does.
        """
        shown = self._snapshot.board
        rows = len(reading.cells)
        if shown is None or (shown.height, shown.width) != (rows, len(reading.cells[0])):
            return False
        rack = reading.rack_counter()
        before = self._snapshot.rack or Counter()
        if not reading.rack_uncertain and rack != before and rack - before:
            return False
        taken = max(0, sum(before.values()) - sum(rack.values()))
        added = 0
        unclear: list[tuple[int, int]] = []
        for r, row in enumerate(reading.cells):
            for c, cell in enumerate(row):
                if cell.state == UNSEEN or shown.is_unknown(r, c):
                    continue
                was = shown.cells[r][c]
                if cell.uncertain:
                    # an unclear read of a known tile is fine; a new tile-like glyph is not
                    if was is None and cell.letter is not None:
                        unclear.append((r, c))
                    continue
                now = (cell.letter, cell.blank) if cell.letter else None
                if now == was:
                    continue
                if was is None and now is not None and cell.state != RECENT:
                    added += 1  # placed by the player; a highlighted tile was just played
                    continue
                return False
        # while tiles are out of the rack, unclear new squares are those tiles: placed, or in
        # the player's hand overlapping up to four squares. With the rack unchanged a new tile
        # is someone else's move.
        if unclear and (taken == 0 or len(unclear) > 4 * taken):
            return False
        return added <= taken

    def _placing(self, reading: Reading, rack: Counter) -> bool:
        """Tiles moved from the rack onto the board but not yet submitted.

        Only needed where placed and submitted tiles look the same; elsewhere the tentative
        state already says so.
        """
        if self.last_confirmed is None or reading.rack_uncertain:
            return False
        before, before_rack, _ = self.last_confirmed
        taken = sum(before_rack.values()) - sum(rack.values())
        if taken <= 0 or rack - before_rack:
            return False
        seen = reading.placed()
        if (seen.width, seen.height) != (before.width, before.height):
            return False
        added = sum(
            1
            for r in range(seen.height)
            for c in range(seen.width)
            if not seen.is_unknown(r, c)
            and seen.cells[r][c] is not None
            and before.cells[r][c] is None
        )
        return 0 < added <= taken

    def _check_detail(self, reading: Reading) -> str:
        if SCALE_CHANGED in reading.warnings:
            return "Board size changed; recalibrate"
        parts = []
        n = len(reading.uncertain_cells)
        if n:
            parts.append(f"{n} square{'s' if n != 1 else ''} to check")
        if reading.rack_uncertain:
            parts.append("rack to check")
        parts += [w for w in reading.warnings]
        return "; ".join(parts) or "Check tiles"

    def _confirm(self, board: Board, rack: Counter, order: list[str], now: float) -> None:
        self._confirmed_rack_size = max(sum(rack.values()), self._rack_tiles)
        if self._tiles_removed(board):
            # tiles never leave the board during a game, so this is a new game
            self.memory.forget()
            new_game = getattr(self.recognizer, "new_game", None)
            if new_game is not None:
                new_game()
        board, coverage = self.memory.update(board, rack)
        self.last_confirmed = (board, Counter(rack), order)
        self._confirmed_at = now
        pid = position_id(board, rack, self.profile, self.lexicon_version)
        base = dict(pid=pid, board=board, rack=Counter(rack), confirmed_at=now, coverage=coverage)
        if pid in self.cache:
            self.cache.move_to_end(pid)
            self._set(
                Status.READY, "", result=self.cache[pid], published_at=now, solve_ms=0.0, **base
            )
            return
        if not rack:
            empty = SearchResult([], True, 0, 0.0)
            self._set(Status.READY, "Rack is empty", result=empty, published_at=now, **base)
            return
        self._submitted_at = now
        self.searches_started += 1
        self.solver.submit(pid, board.to_rows(), rack_to_str(rack), board.stamp_list())
        if self._showing() and self._refines(board, rack):
            snap = self._snapshot
            self._refining = (pid, base)
            self._set(
                Status.READY,
                "Updating",
                result=snap.result,
                published_at=snap.published_at,
                solve_ms=snap.solve_ms,
                **self._keep(snap),
            )
            return
        self._refining = None
        self._set(Status.SOLVING, "Solving", **base)

    def _refines(self, board: Board, rack: Counter) -> bool:
        """The new board only reveals squares the shown one did not know; same rack."""
        shown = self._snapshot.board
        if self._snapshot.rack != rack or (shown.width, shown.height) != (
            board.width,
            board.height,
        ):
            return False
        return all(
            shown.is_unknown(r, c) or board.cells[r][c] == shown.cells[r][c]
            for r in range(board.height)
            for c in range(board.width)
        )

    def _drain(self, now: float) -> None:
        outcomes = self.solver.poll()
        fatal = getattr(self.solver, "fatal", None)
        if fatal and self._snapshot.status == Status.SOLVING:
            self._set(Status.CHECK, f"Solver stopped: {fatal}", **self._keep(self._snapshot))
        for outcome in outcomes:
            res = outcome.result
            if res is not None and res.complete:
                self.cache[outcome.pid] = res
                self.cache.move_to_end(outcome.pid)
                while len(self.cache) > CACHE_SIZE:
                    self.cache.popitem(last=False)
            snap = self._snapshot
            refining = self._refining
            if refining is not None and outcome.pid == refining[0] and res is not None:
                self._refining = None
                if snap.status == Status.READY and snap.detail == "Updating":
                    self._set(
                        Status.READY,
                        "",
                        result=res,
                        published_at=now,
                        solve_ms=res.elapsed * 1000,
                        **refining[1],
                    )
                    continue
            if snap.status != Status.SOLVING or outcome.pid != snap.pid:
                self.stale_discarded += 1
                continue
            if res is None:
                self._set(Status.CHECK, f"Search failed: {outcome.error}", **self._keep(snap))
                continue
            self._set(
                Status.READY,
                "",
                result=res,
                published_at=now,
                solve_ms=res.elapsed * 1000,
                **self._keep(snap),
            )

    @staticmethod
    def _keep(snap: Snapshot) -> dict:
        return dict(
            pid=snap.pid,
            board=snap.board,
            rack=snap.rack,
            confirmed_at=snap.confirmed_at,
            coverage=snap.coverage,
        )

    def apply_corrections(
        self,
        board_fixes: dict[tuple[int, int], CellFix | tuple[str, bool] | None],
        rack: str | None = None,
        rack_fixes: dict[int, str] | None = None,
    ) -> bool:
        """Apply the correction grid. Returns True when the position could be confirmed."""
        reading = self._check_reading
        if reading is None or self._stable_frame is None:
            return False
        frame = self._stable_frame
        self.recognizer.learn_from_corrections(frame, reading, board_fixes, rack_fixes)
        consolidate = getattr(self.recognizer, "consolidate", None)
        if consolidate is not None:
            consolidate(reading, confirmed=True)
        fixes = {k: as_fix(v) for k, v in board_fixes.items()}
        cells = []
        for r, row in enumerate(reading.cells):
            out = []
            for c, cell in enumerate(row):
                fix = fixes.get((r, c))
                if fix is None and cell.state == UNSEEN:
                    out.append(WALL_CELL)
                elif fix is not None:
                    out.append((fix.letter, fix.blank) if fix.letter and fix.played else None)
                elif cell.uncertain:
                    return False
                else:
                    played = cell.letter and cell.state not in ("tentative", "unknown")
                    out.append((cell.letter, cell.blank) if played else None)
            cells.append(out)
        board = Board(len(cells[0]), len(cells), cells, reading.placed().stamps)
        if rack is not None:
            letters = [ch.upper() if ch != BLANK else BLANK for ch in rack if not ch.isspace()]
        else:
            letters = [BLANK if t.blank else t.letter for t in reading.rack]
            for i, ch in (rack_fixes or {}).items():
                if i < len(letters):
                    letters[i] = ch
            if any(t.uncertain for i, t in enumerate(reading.rack) if i not in (rack_fixes or {})):
                return False
        if any(not ch for ch in letters):
            return False
        rack_counter = Counter(letters)
        self._check_reading = None
        again = self.recognizer.read(frame)
        if again.confident and again.placed() == board and again.rack_counter() == rack_counter:
            self._stable_sig = again.signature()
            self._candidate_sig = self._stable_sig
        self._confirm(board, rack_counter, letters, self.clock())
        return True

    def recalibrated(self) -> None:
        """Call after the recognizer's calibration was replaced or updated."""
        self._change = None
        self.rescan()


def snapshot_age(snap: Snapshot, now: float) -> float | None:
    return None if snap.published_at is None else now - snap.published_at


__all__ = ["WAITING", "LiveController", "Snapshot", "Status", "snapshot_age"]
