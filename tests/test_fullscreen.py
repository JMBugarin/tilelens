"""A TileLens window must stay visible over another app's full-screen Space.

Ordinary Qt tool windows are kept out of other apps' full-screen Spaces by macOS, which hid the
panel and overlay over a full-screen game. This opens a real full-screen window in another
process and checks the window the adapter prepared is still on screen. It switches Spaces for a
few seconds, so it only runs when asked:
TILELENS_TEST_FULLSCREEN=1 uv run pytest tests/test_fullscreen.py
"""

import os
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = [
    pytest.mark.gui,
    pytest.mark.slow,
    pytest.mark.skipif(
        sys.platform != "darwin" or not os.environ.get("TILELENS_TEST_FULLSCREEN"),
        reason="switches Spaces; set TILELENS_TEST_FULLSCREEN=1",
    ),
]

FULLSCREEN = textwrap.dedent(
    """
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication([])
    w = QLabel("full screen")
    w.showFullScreen()
    QTimer.singleShot(12000, app.quit)
    app.exec()
    """
)

WINDOW = textwrap.dedent(
    """
    import sys
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    from tilelens.platforms.macos import MacAdapter
    app = QApplication([])
    w = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
    w.setFixedSize(321, 123)
    w.show()
    adapter = MacAdapter()
    QTimer.singleShot(300, lambda: print("PREPARED", adapter.show_on_all_spaces(int(w.winId())),
                                         flush=True))
    QTimer.singleShot(14000, app.quit)
    app.exec()
    """
)


def on_screen(pid: int) -> bool | None:
    import Quartz

    for win in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, 0):
        if int(win["kCGWindowOwnerPID"]) == pid and int(win["kCGWindowBounds"]["Width"]) == 321:
            return bool(win.get("kCGWindowIsOnscreen"))
    return None


def test_window_stays_visible_over_a_fullscreen_space():
    env = {**os.environ, "QT_QPA_PLATFORM": "cocoa"}
    window = subprocess.Popen(
        [sys.executable, "-c", WINDOW], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert window.stdout.readline().startswith("PREPARED True")
        assert on_screen(window.pid) is True
        full = subprocess.Popen([sys.executable, "-c", FULLSCREEN], env=env)
        try:
            time.sleep(5)
            assert on_screen(window.pid) is True
        finally:
            full.terminate()
            full.wait(timeout=10)
    finally:
        window.terminate()
        window.wait(timeout=10)
