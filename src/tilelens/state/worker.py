from __future__ import annotations

import contextlib
import multiprocessing as mp
import queue
import time
from dataclasses import dataclass
from pathlib import Path

from tilelens.rules.board import Board, parse_rack
from tilelens.solver.movegen import MoveGenerator, SearchResult


@dataclass
class SolveOutcome:
    pid: str
    result: SearchResult | None
    error: str | None = None


def _solve(gen: MoveGenerator, rows, rack, stamps, should_stop) -> SearchResult:
    board = Board.from_rows(rows).with_stamps({(r, c): v for r, c, v in stamps})
    return gen.solve(board, parse_rack(rack), should_stop=should_stop)


class InlineSolver:
    """Solves on the calling thread during poll(). Used by tests and the CLI."""

    def __init__(self, generator: MoveGenerator):
        self.gen = generator
        self._pending: tuple | None = None
        self.submitted: list[str] = []

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def submit(self, pid: str, rows: list[str], rack: str, stamps=()) -> None:
        self._pending = (pid, rows, rack, tuple(stamps))
        self.submitted.append(pid)

    def cancel(self) -> None:
        self._pending = None

    def poll(self) -> list[SolveOutcome]:
        if self._pending is None:
            return []
        pid, rows, rack, stamps = self._pending
        self._pending = None
        return [SolveOutcome(pid, _solve(self.gen, rows, rack, stamps, None))]

    def close(self) -> None:
        pass


def _worker_main(profile_path, words_path, jobs, results, generation, time_limit):
    try:
        from tilelens.rules.lexicon import lexicon_for
        from tilelens.rules.profile import load_profile

        profile = load_profile(profile_path)
        lexicon = lexicon_for(profile, words_path)
        gen = MoveGenerator(profile, lexicon, use_cache=True)
    except Exception as exc:
        results.put(("fatal", str(exc)))
        return
    results.put(("ready", lexicon.version))
    parent = mp.parent_process()
    while True:
        try:
            job = jobs.get(timeout=1.0)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                return
            continue
        if job is None:
            return
        g, pid, rows, rack, stamps = job
        if generation.value != g:
            results.put(("cancelled", g, pid))
            continue
        deadline = time.monotonic() + time_limit

        def stop(g=g, deadline=deadline):
            return generation.value != g or time.monotonic() > deadline

        try:
            res = _solve(gen, rows, rack, stamps, stop)
        except Exception as exc:
            results.put(("error", g, pid, str(exc)))
            continue
        if generation.value != g:
            results.put(("cancelled", g, pid))
        else:
            results.put(("done", g, pid, res))


class ProcessSolver:
    """Runs searches in a separate process with one job running and at most one waiting.

    Submitting a new position cancels the running search and replaces whatever was waiting,
    so only the newest position is ever worked on.
    """

    def __init__(
        self,
        profile_path: str | Path,
        words_path: str | Path | None = None,
        time_limit: float = 10.0,
    ):
        ctx = mp.get_context("spawn")
        self._generation = ctx.Value("q", 0)
        self._jobs = ctx.Queue()
        self._results = ctx.Queue()
        self._proc = ctx.Process(
            target=_worker_main,
            args=(
                str(profile_path),
                None if words_path is None else str(words_path),
                self._jobs,
                self._results,
                self._generation,
                time_limit,
            ),
            daemon=True,
            name="tilelens-solver",
        )
        self._proc.start()
        self._running: str | None = None
        self._pending: tuple | None = None
        self.ready = False
        self.lexicon_version: str | None = None
        self.fatal: str | None = None
        self.cancelled: list[str] = []

    @property
    def busy(self) -> bool:
        return self._running is not None or self._pending is not None

    def _send(self, pid, rows, rack, stamps) -> None:
        self._running = pid
        self._jobs.put((self._generation.value, pid, rows, rack, stamps))

    def submit(self, pid: str, rows: list[str], rack: str, stamps=()) -> None:
        stamps = tuple(stamps)
        if self._running is not None:
            with self._generation.get_lock():
                self._generation.value += 1
            self._pending = (pid, rows, rack, stamps)
        else:
            self._send(pid, rows, rack, stamps)

    def cancel(self) -> None:
        self._pending = None
        if self._running is not None:
            with self._generation.get_lock():
                self._generation.value += 1

    def poll(self) -> list[SolveOutcome]:
        out: list[SolveOutcome] = []
        while True:
            try:
                msg = self._results.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "ready":
                self.ready = True
                self.lexicon_version = msg[1]
                continue
            if kind == "fatal":
                self.fatal = msg[1]
                self._running = None
                self._pending = None
                continue
            _, _g, pid = msg[:3]
            if pid == self._running:
                self._running = None
            if kind == "done":
                out.append(SolveOutcome(pid, msg[3]))
            elif kind == "error":
                out.append(SolveOutcome(pid, None, msg[3]))
            else:
                self.cancelled.append(pid)
            if self._running is None and self._pending is not None:
                job, self._pending = self._pending, None
                self._send(*job)
        if self._running is not None and not self._proc.is_alive():
            self.fatal = self.fatal or "solver process stopped"
            self._running = None
        return out

    def wait_ready(self, timeout: float = 30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.ready and not self.fatal:
            try:
                msg = self._results.get(timeout=0.05)
            except queue.Empty:
                continue
            if msg[0] == "ready":
                self.ready, self.lexicon_version = True, msg[1]
            elif msg[0] == "fatal":
                self.fatal = msg[1]
            else:
                self._results.put(msg)
        return self.ready

    def close(self) -> None:
        self.cancel()
        with contextlib.suppress(OSError, ValueError):
            self._jobs.put(None)
        self._proc.join(timeout=2)
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=2)
