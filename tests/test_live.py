import copy
import random
import time

import numpy as np
import pytest

from tilelens.capture.change import ChangeDetector
from tilelens.capture.source import CallableSource
from tilelens.rules.profile import demo_profile_dir
from tilelens.state.controller import WAITING, LiveController, Status
from tilelens.state.position import position_id
from tilelens.state.worker import InlineSolver, ProcessSolver, SolveOutcome
from tilelens.synth.game import FakeGame, calibration_samples
from tilelens.vision.recognizer import CellFix, Recognizer, calibrate


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DelayedSolver:
    """Finishes each search a fixed number of polls after it starts; newest job wins."""

    def __init__(self, gen, delay=3):
        self.inner = InlineSolver(gen)
        self.delay = delay
        self.running = None
        self.pending = None
        self.submitted = []

    @property
    def busy(self):
        return self.running is not None

    def submit(self, pid, rows, rack):
        self.submitted.append(pid)
        if self.running is not None:
            self.pending = (pid, rows, rack)
        else:
            self.running = [self.delay, (pid, rows, rack)]

    def cancel(self):
        self.pending = None

    def poll(self):
        if self.running is None:
            return []
        self.running[0] -= 1
        if self.running[0] > 0:
            return []
        pid, rows, rack = self.running[1]
        self.inner.submit(pid, rows, rack)
        out = self.inner.poll()
        self.running = None
        if self.pending is not None:
            self.running, self.pending = [self.delay, self.pending], None
        return out

    def close(self):
        pass


@pytest.fixture(scope="module")
def calibration():
    samples, hint = calibration_samples([71, 72, 73], theme="meadow", cell=36)
    game = FakeGame(seed=71)
    return calibrate(samples, game.profile, board_hint=hint)


class Harness:
    def __init__(self, calibration, seed=1, solver_delay=0, theme="meadow"):
        self.game = FakeGame(seed=seed, theme=theme)
        self.clock = Clock()
        self.recognizer = Recognizer(copy.deepcopy(calibration), self.game.profile)
        self.reads = 0
        original = self.recognizer.read

        def counted(img):
            self.reads += 1
            return original(img)

        self.recognizer.read = counted
        gen = self.game.gen
        self.solver = DelayedSolver(gen, solver_delay) if solver_delay else InlineSolver(gen)
        self.published = []
        self.violations = []
        self.corrections = 0
        self.lexicon_version = self.game.lexicon.version
        self.ctrl = LiveController(
            CallableSource(self.game.frame),
            self.recognizer,
            self.solver,
            self.game.profile,
            self.lexicon_version,
            clock=self.clock,
            on_update=self.check,
        )

    def truth_pid(self):
        board, rack = self.game.position()
        return position_id(board, rack, self.game.profile, self.lexicon_version)

    def check(self, snap):
        if snap.result is not None:
            self.published.append(snap.pid)
            if snap.pid != self.truth_pid():
                self.violations.append((self.clock.t, snap.pid, self.truth_pid()))

    def tick(self, n=1):
        for _ in range(n):
            self.clock.t += 0.1
            self.ctrl.tick()
            snap = self.ctrl.snapshot
            if snap.result is not None and snap.pid != self.truth_pid():
                self.violations.append(("after tick", self.clock.t))

    def run(self, action, settle=6):
        for _ in action:
            self.tick()
        self.tick(settle)

    def settle(self, limit=30, fix=True):
        for _ in range(limit):
            self.tick()
            snap = self.ctrl.snapshot
            if snap.status == Status.CHECK and fix and self.correct(snap):
                self.corrections += 1
                continue
            if snap.status in (Status.READY, Status.CHECK):
                return snap
        return self.ctrl.snapshot

    def correct(self, snap):
        """Answer the correction grid the way a user would, from the true position."""
        reading = snap.reading
        if reading is None:
            return False
        board = self.game.board
        fixes = {}
        for r, c in reading.uncertain_cells:
            if (r, c) in self.game.tentative:
                letter, blank = self.game.tentative[(r, c)]
                fixes[(r, c)] = CellFix(letter, blank, played=False)
            else:
                cell = board.get(r, c)
                fixes[(r, c)] = CellFix(cell[0], cell[1]) if cell else CellFix(None)
        truth_rack = [x for x in self.game.rack if x is not None]
        rack_fixes = {i: truth_rack[i] for i, t in enumerate(reading.rack) if t.uncertain}
        if len(reading.rack) != len(truth_rack):
            return False
        return self.ctrl.apply_corrections(fixes, rack_fixes=rack_fixes)


def test_reaches_ready_with_exact_result(calibration):
    h = Harness(calibration)
    snap = h.settle()
    assert snap.status == Status.READY, snap.detail
    board, rack = h.game.position()
    assert snap.board == board and snap.rack == rack
    direct = h.game.gen.solve(board, rack)
    assert [(m.tiles, m.score) for m in snap.result.moves] == [
        (m.tiles, m.score) for m in direct.moves
    ]
    assert snap.result.complete and snap.result.label == "highest scoring"
    assert not h.violations


def test_timer_and_chat_do_not_trigger_reads_or_searches(calibration):
    h = Harness(calibration)
    h.settle()
    reads, searches = h.reads, h.ctrl.searches_started
    for i in range(40):
        h.game.tick_timer()
        if i % 7 == 0:
            h.game.say(f"Bo: message {i}")
        h.tick()
    assert h.reads == reads
    assert h.ctrl.searches_started == searches
    assert h.ctrl.snapshot.status == Status.READY
    assert h.ctrl.interval() == pytest.approx(0.5)


def test_opponent_animation_clears_immediately_and_searches_once(calibration):
    h = Harness(calibration)
    h.settle()
    old_pid = h.ctrl.snapshot.pid
    searches = h.ctrl.searches_started
    anim = h.game.opponent_move(frames=4)
    next(anim)
    h.tick()
    assert h.ctrl.snapshot.result is None
    for _ in anim:
        h.tick()
    snap = h.settle()
    assert snap.status == Status.READY and snap.pid != old_pid
    assert h.ctrl.searches_started == searches + 1
    assert not h.violations


def test_drag_and_tentative_tiles_wait_without_searching(calibration):
    h = Harness(calibration)
    h.settle()
    searches = h.ctrl.searches_started
    statuses = set()
    for _ in h.game.place_best(frames_per_tile=3):
        h.tick()
        statuses.add((h.ctrl.snapshot.status, h.ctrl.snapshot.detail))
        assert h.ctrl.snapshot.result is None
    h.tick(8)
    assert h.ctrl.snapshot.status == Status.READING
    assert h.ctrl.snapshot.detail == WAITING
    assert h.ctrl.searches_started == searches
    h.run(h.game.submit())
    snap = h.settle()
    assert snap.status == Status.READY
    assert snap.pid == h.truth_pid()
    assert not h.violations


def test_recall_returns_to_cached_result(calibration):
    h = Harness(calibration)
    first = h.settle().pid
    searches = h.ctrl.searches_started
    h.run(h.game.place_best())
    h.run(h.game.recall())
    snap = h.settle()
    assert snap.pid == first and snap.status == Status.READY
    assert h.ctrl.searches_started == searches


def test_rack_shuffle_uses_cache(calibration):
    h = Harness(calibration)
    first = h.settle().pid
    searches = h.ctrl.searches_started
    h.run(h.game.shuffle(frames=3))
    snap = h.settle()
    assert snap.status == Status.READY and snap.pid == first
    assert h.ctrl.searches_started == searches
    assert not h.violations


def test_results_for_outdated_positions_are_discarded(calibration):
    h = Harness(calibration, solver_delay=6)
    for _ in range(4):
        h.tick()
    h.run(h.game.opponent_move(frames=2), settle=0)
    for _ in range(6):
        h.tick()
    h.run(h.game.opponent_move(frames=2), settle=0)
    snap = h.settle(60)
    assert snap.status == Status.READY and snap.pid == h.truth_pid()
    assert h.ctrl.stale_discarded >= 1
    assert not h.violations


@pytest.mark.parametrize("seed", range(6))
def test_random_replays_never_publish_stale_results(calibration, seed):
    rng = random.Random(seed)
    h = Harness(calibration, seed=seed + 10, solver_delay=rng.choice([1, 2, 4, 7]))
    for _ in range(10):
        roll = rng.random()
        if roll < 0.3:
            h.run(h.game.opponent_move(frames=rng.randint(1, 4)), settle=rng.randint(0, 5))
        elif roll < 0.5:
            h.run(h.game.shuffle(frames=rng.randint(1, 3)), settle=rng.randint(0, 5))
        elif roll < 0.7:
            h.run(h.game.place_best(frames_per_tile=rng.randint(1, 3)), settle=rng.randint(0, 4))
            if rng.random() < 0.5:
                h.run(h.game.submit(), settle=rng.randint(0, 4))
            else:
                h.run(h.game.recall(), settle=rng.randint(0, 4))
        else:
            for _ in range(rng.randint(1, 8)):
                h.game.tick_timer()
                h.tick()
    if h.game.tentative:
        h.run(h.game.submit())
    snap = h.settle(80)
    assert not h.violations
    assert snap.status in (Status.READY, Status.CHECK)
    if snap.status == Status.READY:
        assert snap.pid == h.truth_pid()


def test_pause_resume_and_not_visible(calibration):
    h = Harness(calibration)
    pid = h.settle().pid
    h.ctrl.pause()
    h.tick(3)
    assert h.ctrl.snapshot.status == Status.PAUSED and h.ctrl.snapshot.result is None
    h.ctrl.resume()
    assert h.settle().pid == pid
    frames = {"blank": False}
    real = h.game.frame
    h.ctrl.source = CallableSource(lambda: np.full_like(real(), 40) if frames["blank"] else real())
    frames["blank"] = True
    h.tick(2)
    assert h.ctrl.snapshot.status == Status.NOT_VISIBLE and h.ctrl.snapshot.result is None
    frames["blank"] = False
    assert h.settle().pid == pid
    h.ctrl.source = CallableSource(lambda: None)
    h.tick()
    assert h.ctrl.snapshot.status == Status.NOT_VISIBLE


def test_corrections_flow(calibration):
    cal = copy.deepcopy(calibration)
    h = Harness(cal)
    board, _ = h.game.position()
    letters = {t.letter for t in board.tiles() if not t.blank}
    missing = sorted(letters)[0]
    h.recognizer.cal.templates.pop(missing)
    h.recognizer._rebuild_templates()
    snap = h.settle(fix=False)
    assert snap.status == Status.CHECK
    reading = snap.reading
    assert reading.uncertain_cells
    assert not h.ctrl.apply_corrections({})
    fixes = {}
    for r, c in reading.uncertain_cells:
        cell = board.get(r, c)
        fixes[(r, c)] = CellFix(cell[0], cell[1]) if cell else CellFix(None)
    rack_fixes = {
        i: (t.letter if not t.blank else "?") for i, t in enumerate(reading.rack) if t.uncertain
    }
    truth_rack = [x for x in h.game.rack if x is not None]
    for i in rack_fixes:
        rack_fixes[i] = truth_rack[i]
    assert h.ctrl.apply_corrections(fixes, rack_fixes=rack_fixes)
    h.tick(3)
    snap = h.ctrl.snapshot
    assert snap.status == Status.READY and snap.pid == h.truth_pid()
    assert not h.violations


def test_rescan_and_interval_bounds(calibration):
    h = Harness(calibration)
    h.settle()
    h.ctrl.rescan()
    assert h.ctrl.snapshot.result is None
    assert h.settle().status == Status.READY
    assert LiveController(None, h.recognizer, h.solver, h.game.profile, "v", hz=50).hz == 12
    assert LiveController(None, h.recognizer, h.solver, h.game.profile, "v", hz=1).hz == 8


def test_change_detector_masks():
    base = np.zeros((100, 100, 3), np.uint8)
    det = ChangeDetector([(0, 0, 100, 100)], masks=[(60, 60, 40, 40)])
    assert det.update(base)
    moved = base.copy()
    moved[70:90, 70:90] = 255
    assert not det.update(moved)
    moved[10:30, 10:30] = 255
    assert det.update(moved)
    outside = ChangeDetector([(0, 0, 50, 50)])
    outside.update(base)
    other = base.copy()
    other[60:100, 60:100] = 200
    assert not outside.update(other)


def test_process_solver_keeps_only_newest():
    from tilelens.rules.board import Board

    profile_path = demo_profile_dir() / "demo_garden.toml"
    solver = ProcessSolver(profile_path)
    try:
        assert solver.wait_ready(60), solver.fatal
        empty = ["." * 13] * 13
        solver.submit("a", empty, "AEINRST")
        solver.submit("b", empty, "AEINRS?")
        solver.submit("c", empty, "TONES??")
        outcomes: list[SolveOutcome] = []
        end = time.monotonic() + 60
        while time.monotonic() < end and not any(o.pid == "c" for o in outcomes):
            outcomes += solver.poll()
            time.sleep(0.01)
        pids = [o.pid for o in outcomes]
        assert pids[-1] == "c" and "b" not in pids
        result = outcomes[-1].result
        assert result.complete and result.moves
        assert Board.from_rows(empty).is_empty()
    finally:
        solver.close()
    assert not solver._proc.is_alive()


def test_solver_failure_is_reported(calibration):
    h = Harness(calibration)

    class Dead(InlineSolver):
        fatal = None

        def poll(self):
            self.fatal = "word list not found"
            return []

    h.ctrl.solver = Dead(h.game.gen)
    snap = h.settle(fix=False)
    assert snap.status == Status.CHECK and "Solver stopped" in snap.detail
    assert snap.result is None
