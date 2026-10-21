"""Measure recognition against synthetic ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field

from tilelens.rules.profile import BLANK
from tilelens.synth.render import TENTATIVE, render
from tilelens.synth.scenes import demo_engine, random_scene
from tilelens.vision.recognizer import COMMITTED, RECENT, Calibration, Reading, calibrate


def truth_rows(labels: dict) -> list[str]:
    rows = []
    for row in labels["board"]:
        line = ""
        for cell in row:
            if cell is None or cell["state"] == TENTATIVE:
                line += "."
            else:
                line += cell["letter"].lower() if cell["blank"] else cell["letter"]
        rows.append(line)
    return rows


def truth_rack(labels: dict) -> str:
    return "".join(BLANK if t["blank"] else t["letter"] for t in labels["rack"] if t)


def hint_from(labels: dict) -> tuple[float, float, float, float]:
    g = labels["geometry"]
    return (g["origin"][0], g["origin"][1], g["cols"] * g["pitch"], g["rows"] * g["pitch"])


def calibrate_on(theme: str, seeds, cell: int, profile_name: str = "demo_garden") -> Calibration:
    profile, _, _ = demo_engine(profile_name)
    samples = []
    hint = None
    for seed in seeds:
        img, labels = render(
            random_scene(seed, profile_name, theme=theme, cell=cell, tentative=False, min_turn=8)
        )
        samples.append((img, truth_rows(labels), truth_rack(labels)))
        hint = hint or hint_from(labels)
    return calibrate(samples, profile, board_hint=hint)


@dataclass
class Score:
    cells: int = 0
    correct: int = 0
    flagged: int = 0
    silent_errors: int = 0
    occupied: int = 0
    occupied_correct: int = 0
    rack_tiles: int = 0
    rack_correct: int = 0
    rack_flagged: int = 0
    rack_silent: int = 0
    positions: int = 0
    positions_exact: int = 0
    positions_flagged: int = 0
    positions_silent: int = 0
    tentative: int = 0
    tentative_as_committed: int = 0
    details: list[str] = field(default_factory=list)

    def merge(self, other: Score) -> None:
        for k, v in vars(other).items():
            if isinstance(v, int):
                setattr(self, k, getattr(self, k) + v)
            else:
                getattr(self, k).extend(v)

    def summary(self) -> dict:
        def pct(a, b):
            return round(100 * a / b, 3) if b else None

        return {
            "cells": self.cells,
            "cell_accuracy_pct": pct(self.correct, self.cells),
            "cell_flagged_pct": pct(self.flagged, self.cells),
            "cell_silent_error_pct": pct(self.silent_errors, self.cells),
            "occupied_cells": self.occupied,
            "occupied_accuracy_pct": pct(self.occupied_correct, self.occupied),
            "rack_tiles": self.rack_tiles,
            "rack_accuracy_pct": pct(self.rack_correct, self.rack_tiles),
            "rack_flagged_pct": pct(self.rack_flagged, self.rack_tiles),
            "rack_silent_error_pct": pct(self.rack_silent, self.rack_tiles),
            "positions": self.positions,
            "position_exact_pct": pct(self.positions_exact, self.positions),
            "position_flagged_pct": pct(self.positions_flagged, self.positions),
            "position_silent_error_pct": pct(self.positions_silent, self.positions),
            "tentative_tiles_read": self.tentative,
            "tentative_read_as_committed": self.tentative_as_committed,
        }


def score_reading(reading: Reading, labels: dict, tag: str = "") -> Score:
    s = Score(positions=1)
    silent = flagged = False
    for r, row in enumerate(labels["board"]):
        for c, truth in enumerate(row):
            got = reading.cells[r][c]
            s.cells += 1
            if truth is not None:
                s.occupied += 1
            if got.uncertain:
                s.flagged += 1
                flagged = True
                continue
            if truth is not None and truth["state"] == TENTATIVE:
                s.tentative += 1
                s.tentative_as_committed += got.state in (COMMITTED, RECENT)
            if truth is None:
                ok = got.letter is None
            elif truth["state"] == TENTATIVE:
                ok = got.letter == truth["letter"] and got.state not in (COMMITTED, RECENT)
            else:
                ok = (
                    got.letter == truth["letter"]
                    and got.blank == truth["blank"]
                    and got.state in (COMMITTED, RECENT)
                )
            if ok:
                s.correct += 1
                s.occupied_correct += truth is not None
            else:
                s.silent_errors += 1
                silent = True
                s.details.append(
                    f"{tag} cell {r},{c}: truth={truth} got={got.letter},{got.blank},{got.state}"
                )
    rack_truth = [t for t in labels["rack"] if t]
    s.rack_tiles = len(rack_truth)
    if len(reading.rack) != len(rack_truth):
        s.rack_silent += len(rack_truth)
        silent = True
        s.details.append(f"{tag} rack count {len(reading.rack)} != {len(rack_truth)}")
    else:
        for truth, got in zip(rack_truth, reading.rack, strict=True):
            if got.uncertain:
                s.rack_flagged += 1
                flagged = True
            elif got.blank == truth["blank"] and (truth["blank"] or got.letter == truth["letter"]):
                s.rack_correct += 1
            else:
                s.rack_silent += 1
                silent = True
                s.details.append(f"{tag} rack: truth={truth} got={got.letter},{got.blank}")
    if reading.warnings:
        flagged = True
    if silent:
        s.positions_silent = 1
    elif flagged:
        s.positions_flagged = 1
    else:
        s.positions_exact = 1
    return s
