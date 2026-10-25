"""Plain-language state of the tracker: a label and what, if anything, the player should do."""

from __future__ import annotations

from dataclasses import dataclass

from tilelens.state.controller import WAITING, Snapshot, Status


@dataclass(frozen=True)
class Phase:
    label: str
    tone: str  # "ok", "busy", "warn", "idle"
    hint: str = ""


def phase_of(snap: Snapshot | None) -> Phase:
    if snap is None:
        return Phase("Setup", "idle", "Open the game, then press Calibrate")
    if snap.held is not None:
        return Phase(
            "Holding",
            "busy",
            "Place your tiles over the ghost tiles. Tracking resumes when your rack refills.",
        )
    st, detail = snap.status, snap.detail
    if st == Status.PAUSED:
        return Phase("Paused", "idle", "Press Resume to continue")
    if st == Status.NOT_VISIBLE:
        low = detail.lower()
        if "permission" in low:
            return Phase("No screen access", "warn", "Allow Screen Recording for TileLens")
        if "not found" in low or "no board" in low:
            return Phase(
                "Board obscured",
                "warn",
                "Bring the board into view: a menu, popup or window is covering it",
            )
        return Phase("Board obscured", "warn", detail or "Bring the game back into view")
    if st == Status.CHECK:
        if snap.unsure:
            return Phase("Check one tile", "warn", snap.notice)
        return Phase("Check one tile", "warn", detail)
    if st == Status.READING:
        if detail == WAITING:
            return Phase("Placing tiles", "busy", "Suggestion returns once you submit")
        if detail == "Aligning":
            return Phase("Aligning", "busy")
        return Phase("Reading rack", "busy")
    if st == Status.SOLVING:
        return Phase("Calculating", "busy")
    if detail == WAITING:
        return Phase("Placing tiles", "busy", "This suggestion stays until you submit")
    if snap.unsure:
        return Phase("Tracking", "ok", snap.notice)
    return Phase("Tracking", "ok", snap.notice)
