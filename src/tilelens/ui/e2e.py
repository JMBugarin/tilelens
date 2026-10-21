"""End-to-end runs of the whole app against the fake game window.

Used by `benchmarks/bench_e2e.py` and `tests/test_e2e.py`. In "screen" mode the fake game is a
real window and frames come from real screen capture, so the overlay, permissions and display
scaling are all in the loop. In "inproc" mode frames come straight from the fake game, which
needs no permission.
"""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tilelens.capture.source import CallableSource
from tilelens.platforms import get_adapter
from tilelens.state.controller import Status
from tilelens.state.position import position_id
from tilelens.synth.game import FakeGame
from tilelens.ui.fakegame import FakeGameWindow
from tilelens.ui.session import LiveSession
from tilelens.ui.settings import Settings
from tilelens.vision.recognizer import CellFix, calibrate


@dataclass
class RunReport:
    mode: str
    overlay_mode: str
    events: int = 0
    position_changes: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    stale_published: int = 0
    checks_corrected: int = 0
    unresolved: int = 0
    overlay_leak_checks: int = 0
    overlay_leak_max_diff: float = 0.0
    suppress_ms: list[float] = field(default_factory=list)
    final_status: str = ""
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    timeline: list = field(default_factory=list)

    def summary(self) -> dict:
        lat = sorted(self.latencies_ms)

        def pct(p):
            return round(lat[min(len(lat) - 1, round(p * (len(lat) - 1)))], 1) if lat else None

        out = {
            "mode": self.mode,
            "overlay_mode": self.overlay_mode,
            "game_events": self.events,
            "position_changes_measured": len(lat),
            "latency_median_ms": round(statistics.median(lat), 1) if lat else None,
            "latency_p95_ms": pct(0.95),
            "latency_worst_ms": round(lat[-1], 1) if lat else None,
            "stale_results_published": self.stale_published,
            "check_tiles_answered": self.checks_corrected,
            "changes_never_resolved": self.unresolved,
            "overlay_leak_checks": self.overlay_leak_checks,
            "overlay_leak_max_mean_diff": round(self.overlay_leak_max_diff, 2),
            "final_status": self.final_status,
            "elapsed_s": round(self.elapsed_s, 1),
            "notes": self.notes,
        }
        if self.suppress_ms:
            out["suppress_hidden_ms_median"] = round(statistics.median(self.suppress_ms), 2)
            out["suppress_hidden_ms_worst"] = round(max(self.suppress_ms), 2)
        return out


def _calibrate_on_window(window: FakeGameWindow, grab, profile, seeds) -> object:
    original = window.game
    samples = []
    for seed in seeds:
        game = FakeGame(seed=seed, theme=original.theme, cell=original.cell, opening_turns=10)
        window.show_game(game)
        _pump(0.25)
        frame = grab()
        samples.append((frame, game.board.to_rows(), "".join(t for t in game.rack if t)))
    window.show_game(original)
    _pump(0.2)
    geo = original.geometry
    fh, fw = samples[0][0].shape[:2]
    sx = fw / window.view.width()
    sy = fh / window.view.height()
    hint = (
        geo.origin[0] * sx,
        geo.origin[1] * sy,
        geo.cols * geo.pitch * sx,
        geo.rows * geo.pitch * sy,
    )
    try:
        return calibrate(samples, profile, board_hint=hint)
    except Exception:
        debug = os.environ.get("TILELENS_E2E_DEBUG")
        if debug:
            for i, (frame, _, _) in enumerate(samples):
                cv2.imwrite(os.path.join(debug, f"cal-{i}.png"), frame)
        raise


def _pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def run(
    mode: str = "inproc",
    events: int = 20,
    seed: int = 3,
    overlay_mode: str = "exclude",
    timeout: float = 240.0,
    step_ms: int = 100,
) -> RunReport:
    app = QApplication.instance() or QApplication([])
    report = RunReport(mode, overlay_mode)
    window = FakeGameWindow(seed=seed, step_ms=step_ms, script=False)
    window.move(80, 80)
    window.show()
    window.raise_()
    _pump(0.5)
    adapter = get_adapter() if mode == "screen" else None
    if mode == "screen":
        if adapter.screen_permission() is False:
            report.notes.append("Screen Recording permission missing; nothing was captured")
            window.close()
            return report

        def grab():
            return adapter.capture(window.capture_region())

        probe = grab()
        if probe is None or float(probe.std()) < 1.0:
            report.notes.append(
                "screen capture returned a blank image (display asleep, locked or covered)"
            )
            window.close()
            return report
    else:
        grab = window.current_frame

    settings = Settings(overlay_mode=overlay_mode)
    session_kwargs = dict(region_provider=window.capture_region, use_process=True)
    if mode == "screen":
        settings.region = window.capture_region().to_dict()
        session = LiveSession(settings, adapter=adapter, **session_kwargs)
    else:
        session = LiveSession(settings, source=CallableSource(grab), **session_kwargs)
    cal = _calibrate_on_window(window, grab, session.profile, range(71, 79))
    session.recognizer = None
    session.settings.calibration = None
    from tilelens.vision.recognizer import Recognizer

    session.recognizer = Recognizer(cal, session.profile)
    lexicon_version = session.lexicon.version

    def pid_of(position):
        if position is None:
            return None
        board, rack = position
        return position_id(board, rack, session.profile, lexicon_version)

    def truth_pid():
        return pid_of(window.game.position())

    captured = {"pids": set()}

    class Recording:
        def __init__(self, inner):
            self.inner = inner

        @property
        def last_error(self):
            return getattr(self.inner, "last_error", None)

        def grab(self):
            pids = {pid_of(window.frame_position)}
            if mode == "screen":
                pids.add(pid_of(window.previous_position))
            frame = self.inner.grab()
            captured["pids"] = pids
            return frame

    pending: dict = {}
    last_ready_pid = {"pid": None}

    def on_update(snap):
        now = time.monotonic()
        report.timeline.append((round(now - t0, 3), snap.status.value, snap.detail))
        if snap.result is not None and snap.status == Status.READY:
            if snap.pid not in captured["pids"]:
                report.stale_published += 1
                tb, tr = window.frame_position
                diffs = [
                    (r, c, snap.board.get(r, c), tb.get(r, c))
                    for r in range(tb.height)
                    for c in range(tb.width)
                    if snap.board.get(r, c) != tb.get(r, c)
                ]
                report.notes.append(
                    f"mismatch at {now - t0:.2f}s: board {diffs[:4]} rack "
                    f"{dict(snap.rack)} vs {dict(tr)}"
                )
            if pending.get("pid") == snap.pid:
                report.latencies_ms.append((now - pending["t"]) * 1000)
                pending.clear()
            last_ready_pid["pid"] = snap.pid
        if snap.status == Status.CHECK and snap.reading is not None:
            QTimer.singleShot(0, lambda s=snap: answer_check(s))

    def answer_check(snap):
        ctrl = session.controller
        if ctrl is None or ctrl.snapshot is not snap:
            return
        reading = snap.reading
        fixes = {}
        for r, c in reading.uncertain_cells:
            if (r, c) in window.game.tentative:
                letter, blank = window.game.tentative[(r, c)]
                fixes[(r, c)] = CellFix(letter, blank, played=False)
            else:
                cell = window.game.board.get(r, c)
                fixes[(r, c)] = CellFix(cell[0], cell[1]) if cell else CellFix(None)
        truth_rack = [x for x in window.game.rack if x is not None]
        if len(truth_rack) == len(reading.rack):
            rack_fixes = {i: truth_rack[i] for i, t in enumerate(reading.rack) if t.uncertain}
            if ctrl.apply_corrections(fixes, rack_fixes=rack_fixes):
                report.checks_corrected += 1

    def on_step(name):
        report.events += 1
        pid = truth_pid()
        if pending:
            report.unresolved += 1
            pending.clear()
        if pid != last_ready_pid["pid"]:
            pending.update(pid=pid, t=window.last_change_at)
            report.position_changes += 1

    session.updated.connect(on_update)
    window.stepped.connect(on_step)
    t0 = time.monotonic()
    session.start()
    session.controller.source = Recording(session.controller.source)
    _pump(2.0)
    window.timer.start(step_ms)

    leak_timer = QTimer()

    def check_leak():
        ov = session.overlay
        if mode != "screen" or not ov.isVisible() or ov.move_shown is None:
            return
        frame = grab()
        ref = window.current_frame()
        if frame is None or ref is None or ov.move_shown is None:
            return
        if frame.shape != ref.shape:
            frame = cv2.resize(frame, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
        mapper = session.overlay_mapper()
        diffs = []
        for t in ov.move_shown.tiles:
            rect = mapper.rect(t.row, t.col)
            x, y, w, h = int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())
            a = frame[y : y + h, x : x + w].astype(int)
            b = ref[y : y + h, x : x + w].astype(int)
            if a.size and a.shape == b.shape:
                diffs.append(float(np.abs(a - b).mean()))
        if diffs:
            report.overlay_leak_checks += 1
            report.overlay_leak_max_diff = max(report.overlay_leak_max_diff, max(diffs))

    leak_timer.timeout.connect(check_leak)
    leak_timer.start(700)
    while report.events < events and time.monotonic() - t0 < timeout:
        app.processEvents()
        time.sleep(0.002)
    window.timer.stop()
    _pump(3.0)
    leak_timer.stop()
    report.final_status = session.snapshot.status.value if session.snapshot else "none"
    report.suppress_ms = [d * 1000 for d in session.overlay.suppress_log]
    report.elapsed_s = time.monotonic() - t0
    session.stop()
    window.close()
    _pump(0.2)
    return report
