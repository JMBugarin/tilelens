import json
import sys
from pathlib import Path

import pytest

from tilelens import telemetry

sys.path.insert(0, str(Path(__file__).parents[1] / "benchmarks"))
from analyze_trace import summarize


@pytest.fixture
def fresh_trace(monkeypatch):
    monkeypatch.setattr(telemetry, "_trace", None)
    monkeypatch.setattr(telemetry, "_checked", False)


def test_emit_is_silent_without_the_variable(fresh_trace, monkeypatch):
    monkeypatch.delenv("TILELENS_TRACE", raising=False)
    assert not telemetry.enabled()
    telemetry.emit("frame", changed=True)


def test_emit_appends_json_lines(fresh_trace, monkeypatch, tmp_path):
    path = tmp_path / "t.jsonl"
    monkeypatch.setenv("TILELENS_TRACE", str(path))
    assert telemetry.enabled()
    telemetry.emit("frame", grab_ms=1.5, changed=True)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["ev"] for x in lines] == ["start", "frame"]
    assert lines[1]["grab_ms"] == 1.5 and lines[1]["t"] >= lines[0]["t"]


def ev(name, t, **kw):
    return {"ev": name, "t": t, **kw}


def test_an_episode_runs_from_the_first_changed_frame_to_the_paint():
    events = [
        ev("frame", 0.0, changed=False, grab_ms=20),
        ev("frame", 1.0, changed=True, grab_ms=21),
        ev("read", 1.0, read_ms=30, relocated=False),
        ev("frame", 1.1, changed=True, grab_ms=21),
        ev("stable", 1.3, frames=3),
        ev("publish", 1.35, cached=False, solve_ms=12.0, moves=3),
        ev("paint", 1.36, paint_ms=4.0, kind="overlay"),
        ev("frame", 5.0, changed=True, grab_ms=20),
        ev("read", 5.0, read_ms=30, relocated=True),
    ]
    out = summarize(events)
    assert out["episodes"] == 2 and out["episodes_with_result"] == 1
    assert out["episodes_without_result_at"] == [5.0]
    assert out["change_to_stable_ms"]["median_ms"] == 300.0
    assert out["solve_ms"]["median_ms"] == 12.0
    assert out["change_to_result_painted_ms"]["median_ms"] == pytest.approx(364.0, abs=1)
    assert out["relocations"] == 1
