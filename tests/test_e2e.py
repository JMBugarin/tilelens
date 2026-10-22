import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.gui, pytest.mark.slow]


def run_mode(mode: str, events: int, platform: str) -> dict:
    code = textwrap.dedent(
        f"""
        import json
        from tilelens.ui.e2e import run

        if __name__ == "__main__":
            report = run({mode!r}, events={events})
            print("REPORT" + json.dumps(report.summary()))
        """
    )
    env = {**os.environ, "QT_QPA_PLATFORM": platform}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=env
    )
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("REPORT")), None)
    assert line, out.stderr[-2000:]
    import json

    return json.loads(line[len("REPORT") :])


def test_end_to_end_in_process():
    r = run_mode("inproc", 10, "offscreen")
    assert r["stale_results_published"] == 0
    assert r["position_changes_measured"] >= 3
    assert r["latency_worst_ms"] < 3000
    assert r["final_status"] in ("Ready", "Check tiles")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS screen capture")
def test_end_to_end_on_screen():
    r = run_mode("screen", 10, "cocoa")
    if any("permission" in n or "blank image" in n or "covered" in n for n in r["notes"]):
        pytest.skip("; ".join(r["notes"]))
    assert r["stale_results_published"] == 0
    assert r["position_changes_measured"] >= 3
    assert r["overlay_leak_checks"] > 0
    assert r["overlay_leak_max_mean_diff"] < 6
