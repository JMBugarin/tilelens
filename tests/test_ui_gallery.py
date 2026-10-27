import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtGui import QImage  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@pytest.fixture(scope="module")
def gallery():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from ui_gallery import registry, run

    run.load_states()
    return run, registry


def run_args(out: Path, *only: str, backdrop: str = "discord", extra: tuple = ()) -> list[str]:
    args = ["--out", str(out), "--backdrop", backdrop, "--scale", "1", "--settle", "0", *extra]
    for name in only:
        args += ["--only", name]
    return args


def pixels(path: Path) -> np.ndarray:
    img = QImage(str(path))
    assert not img.isNull(), path
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * img.height())
    return arr.reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4].reshape(-1, 4).copy()


def test_two_states_render_to_non_blank_pngs(gallery, tmp_path):
    run, _ = gallery
    code = run.main(
        [
            "--out",
            str(tmp_path),
            "--only",
            "panel/no-move",
            "--only",
            "overlay/ghosts-crossing",
            "--backdrop",
            "discord",
            "--scale",
            "1",
            "--settle",
            "0",
        ]
    )
    assert code == 0
    panel = tmp_path / "panel" / "no-move__discord.png"
    overlay = tmp_path / "overlay" / "ghosts-crossing__discord.png"
    for path in (panel, overlay):
        assert path.is_file() and path.stat().st_size > 2000
        px = pixels(path)
        assert len(np.unique(px, axis=0)) > 20
        assert px[:, :3].std() > 5
    # the glass is translucent, so the backdrop shows through and the window is not the backdrop
    assert (tmp_path / "index.html").is_file()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert not manifest["failures"]
    assert {r["state"] for r in manifest["images"]} == {"panel/no-move", "overlay/ghosts-crossing"}


def test_every_registered_name_is_well_formed(gallery):
    _, registry = gallery
    assert len(registry.REGISTRY) > 40
    assert all(registry.NAME.match(name) for name in registry.REGISTRY)


def test_register_rejects_bad_names_and_duplicates(gallery):
    _, registry = gallery
    with pytest.raises(ValueError):
        registry.register("Bad Name")(lambda: None)
    with pytest.raises(ValueError):
        registry.register("panel/ready", kind="sideways")(lambda: None)
    with pytest.raises(ValueError):
        registry.register("panel/ready")(lambda: None)


def test_a_run_deletes_the_windows_it_built(gallery, tmp_path):
    from PySide6.QtWidgets import QApplication

    run, _ = gallery
    run.main(run_args(tmp_path, "window/main-ready", "panel/no-move"))
    assert len(QApplication.allWidgets()) == 0


def test_an_exception_inside_a_qt_callback_fails_the_state(gallery, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget

    run, registry = gallery

    def build():
        widget = QWidget()
        QTimer.singleShot(0, lambda: 1 / 0)
        return widget

    registry.register("test-only/callback-error", kind="dialog")(build)
    try:
        code = run.main(run_args(tmp_path, "test-only/callback-error", extra=("--strict",)))
    finally:
        del registry.REGISTRY["test-only/callback-error"]
    assert code == 1
    failure = json.loads((tmp_path / "manifest.json").read_text())["failures"][0]
    assert failure["state"] == "test-only/callback-error"
    assert "ZeroDivisionError" in failure["error"]


def test_a_filter_that_matches_nothing_is_an_error(gallery, tmp_path):
    run, _ = gallery
    assert run.main(run_args(tmp_path, "no-such-state")) == 2
    assert not (tmp_path / "index.html").exists()


def test_a_run_gives_back_the_application_style(gallery, tmp_path):
    pytest.importorskip("tilelens.ui.design")
    from PySide6.QtWidgets import QApplication

    from tilelens.ui.design import GlassWindow, reduce_motion

    run, _ = gallery
    app = QApplication.instance() or QApplication(["ui-gallery-test"])
    kept = (
        app.style().name(),
        app.styleSheet(),
        app.palette().window().color().rgba(),
        app.font().pixelSize(),
        reduce_motion(),
        vars(GlassWindow).get("backdrop"),
    )
    extra = ("--app-style", "design", "--scheme", "dark", "--blur")
    assert run.main(run_args(tmp_path, "panel/no-move", extra=extra)) == 0
    assert kept == (
        app.style().name(),
        app.styleSheet(),
        app.palette().window().color().rgba(),
        app.font().pixelSize(),
        reduce_motion(),
        vars(GlassWindow).get("backdrop"),
    )


def test_fractional_scale_renders_at_that_ratio(tmp_path):
    script = TOOLS / "ui_gallery" / "run.py"
    args = [sys.executable, str(script), "--out", str(tmp_path), "--only", "panel/no-move"]
    args += ["--backdrop", "gradient", "--scale", "1.5", "--settle", "0"]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    rec = json.loads((tmp_path / "manifest.json").read_text())["images"][0]
    assert abs(rec["size_px"][0] - rec["size_pt"][0] * 1.5) <= 1.5
    assert abs(rec["size_px"][1] - rec["size_pt"][1] * 1.5) <= 1.5


def test_compare_pairs_an_appearance_with_the_unforced_render(tmp_path):
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from ui_gallery import sheet

    earlier = tmp_path / "an earlier run"
    (earlier / "panel").mkdir(parents=True)
    (earlier / "panel" / "ready__discord.png").write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    rec = {
        "state": "panel/ready",
        "kind": "window",
        "doc": "",
        "backdrop": "discord",
        "scheme": "dark",
        "file": "panel/ready__discord__dark.png",
        "size_px": [200, 100],
        "size_pt": [100, 50],
    }
    page = sheet.write_index(out, [rec], [], earlier).read_text(encoding="utf-8")
    assert "an%20earlier%20run/panel/ready__discord.png" in page
    assert "no earlier render" not in page


def test_a_state_that_runs_a_dialog_with_exec_fails_instead_of_hanging(gallery, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    run, registry = gallery

    def build():
        box = QMessageBox()
        guard = QTimer(box)
        guard.setSingleShot(True)
        guard.timeout.connect(box.close)
        guard.start(4000)
        box.exec()
        return box

    registry.register("test-only/exec-dialog", kind="dialog")(build)
    try:
        code = run.main(run_args(tmp_path, "test-only/exec-dialog", extra=("--strict",)))
    finally:
        del registry.REGISTRY["test-only/exec-dialog"]
    assert code == 1
    error = json.loads((tmp_path / "manifest.json").read_text())["failures"][0]["error"]
    assert "QMessageBox" in error and "exec()" in error


def test_a_cleanup_that_raises_fails_the_state(gallery, tmp_path):
    from PySide6.QtWidgets import QWidget
    from ui_gallery import ctx

    run, registry = gallery

    def build():
        ctx.defer(lambda: 1 / 0)
        return QWidget()

    registry.register("test-only/bad-cleanup", kind="dialog")(build)
    try:
        code = run.main(run_args(tmp_path, "test-only/bad-cleanup", extra=("--strict",)))
    finally:
        del registry.REGISTRY["test-only/bad-cleanup"]
    assert code == 1
    failure = json.loads((tmp_path / "manifest.json").read_text())["failures"][0]
    assert "ZeroDivisionError" in failure["error"]


def test_scene_panel_sits_beside_the_board_not_over_it(gallery):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from ui_gallery import ctx, states_scene

    QApplication.instance() or QApplication(["ui-gallery-test"])
    stage = states_scene._scene("ready")
    (overlay, _, _), (panel, (x, _)) = stage.items
    try:
        glass_left = x + states_scene._glass_inset(panel)
        assert glass_left >= ctx.scenario().board_rect.right() + states_scene.GAP - 1
        assert stage.size[0] > ctx.scenario().size_pt[0]
    finally:
        for widget in (overlay, panel):
            widget.close()
            widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_appearance_is_forced_on_a_window_a_state_grabs_itself(gallery, tmp_path):
    design = pytest.importorskip("tilelens.ui.design")
    from PySide6.QtWidgets import QPushButton
    from ui_gallery import ctx

    run, registry = gallery
    seen = []

    def build():
        win = design.GlassWindow()
        button = QPushButton("ok", win)
        button.setToolTip("hint")
        win.resize(240, 120)
        image = ctx.tooltip(win, button)
        seen.append(win.glass_material.name)
        return image

    registry.register("test-only/tooltip-scheme", kind="window")(build)
    try:
        extra = ("--app-style", "design", "--scheme", "light")
        assert run.main(run_args(tmp_path, "test-only/tooltip-scheme", extra=extra)) == 0
    finally:
        del registry.REGISTRY["test-only/tooltip-scheme"]
    assert seen == ["light"]


def test_comparing_a_directory_with_itself_is_ignored(gallery, tmp_path):
    run, _ = gallery
    args = run_args(tmp_path, "panel/no-move", extra=("--compare", str(tmp_path)))
    assert run.main(args) == 0
    assert 'class="pair"' not in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_warnings_outside_the_states_reach_the_index(gallery, tmp_path):
    run, _ = gallery
    run.QT_MESSAGES[:] = ["Could not parse application stylesheet"]
    run.take_notes()
    try:
        assert run.RUN_NOTES == ["Could not parse application stylesheet"]
        from ui_gallery import sheet

        page = sheet.write_index(tmp_path, [], [], None, run.RUN_NOTES).read_text(encoding="utf-8")
    finally:
        run.RUN_NOTES.clear()
    assert "Could not parse application stylesheet" in page


def test_simulated_blur_changes_only_the_pixels_under_the_glass():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor

    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from ui_gallery import compose

    glass = QImage(60, 40, QImage.Format.Format_ARGB32_Premultiplied)
    glass.fill(QColor(20, 20, 30, 200))
    layer = compose.Layer(glass, QPointF(50, 30))
    plain = compose.composite((200, 120), [layer], "gradient", 1.0, False)
    blurred = compose.composite((200, 120), [layer], "gradient", 1.0, True)
    a, b = pixels_of(plain), pixels_of(blurred)
    changed = np.argwhere((a != b).any(axis=2))
    assert len(changed)
    assert changed[:, 0].min() >= 30 - 4 and changed[:, 0].max() <= 70 + 4
    assert changed[:, 1].min() >= 50 - 4 and changed[:, 1].max() <= 110 + 4


def pixels_of(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * img.height())
    view = arr.reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4]
    return view.reshape(img.height(), img.width(), 4).copy()
