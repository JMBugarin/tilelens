import ctypes
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tilelens.platforms import GenericAdapter, macos, windows
from tilelens.platforms.base import PlatformAdapter


class Bare(PlatformAdapter):
    def capture(self, region):
        raise AssertionError

    def screen_permission(self):
        return None


def test_adapters_without_a_native_blur_say_so():
    for adapter in (Bare(), GenericAdapter.__new__(GenericAdapter)):
        assert adapter.apply_backdrop(0x20000, 26, True) is False
        assert adapter.apply_backdrop(0x20000, 26, False, 24) is False
        assert adapter.clear_backdrop(0x20000) is False


class Layer:
    def __init__(self):
        self.radius = None
        self.masks = None
        self.curve = None

    def setCornerRadius_(self, v):
        self.radius = v

    def setMasksToBounds_(self, v):
        self.masks = v

    def setCornerCurve_(self, v):
        self.curve = v


class Effect:
    @classmethod
    def alloc(cls):
        return cls()

    def initWithFrame_(self, frame):
        self.frame = frame
        self.layer_ = Layer()
        self.parent = None
        return self

    def setIdentifier_(self, v):
        self._id = v

    def identifier(self):
        return self._id

    def setBlendingMode_(self, v):
        self.blending = v

    def setState_(self, v):
        self.state = v

    def setMaterial_(self, v):
        self.material = v

    def setAutoresizingMask_(self, v):
        self.mask = v

    def setWantsLayer_(self, v):
        self.wants_layer = v

    def setFrame_(self, frame):
        self.frame = frame

    def setAppearance_(self, v):
        self.appearance = v

    def layer(self):
        return self.layer_

    def removeFromSuperview(self):
        self.parent.subviews_.remove(self)
        self.parent = None


class FrameView:
    def __init__(self):
        self.subviews_ = []

    def subviews(self):
        return list(self.subviews_)

    def addSubview_positioned_relativeTo_(self, view, order, relative):
        view.parent = self
        self.subviews_.insert(0 if order < 0 else len(self.subviews_), view)
        self.order = (order, relative)


class Content:
    def __init__(self, parent, width=300.0, height=200.0):
        self.parent = parent
        self.size = (width, height)

    def superview(self):
        return self.parent

    def frame(self):
        return SimpleNamespace(size=SimpleNamespace(width=self.size[0], height=self.size[1]))


class Window:
    def __init__(self, with_frame_view=True):
        self.frame_view = FrameView() if with_frame_view else None
        self.content = Content(self.frame_view)
        self.opaque = True
        self.background = None

    def contentView(self):
        return self.content

    def setOpaque_(self, v):
        self.opaque = v

    def setBackgroundColor_(self, v):
        self.background = v


@pytest.fixture
def fake_appkit(monkeypatch):
    module = types.ModuleType("AppKit")
    module.NSVisualEffectView = Effect
    module.NSVisualEffectBlendingModeBehindWindow = 0
    module.NSVisualEffectStateActive = 1
    module.NSVisualEffectMaterialHUDWindow = 13
    module.NSVisualEffectMaterialPopover = 6
    module.NSAppearanceNameAqua = "aqua"
    module.NSAppearanceNameDarkAqua = "dark"
    module.NSAppearance = SimpleNamespace(appearanceNamed_=lambda name: f"appearance:{name}")
    module.NSColor = SimpleNamespace(clearColor=lambda: "clear")
    module.NSViewWidthSizable = 2
    module.NSViewHeightSizable = 16
    module.NSWindowBelow = -1

    def inset(rect, dx, dy):
        w, h = rect.size.width - 2 * dx, rect.size.height - 2 * dy
        return SimpleNamespace(size=SimpleNamespace(width=w, height=h), inset=(dx, dy))

    module.NSInsetRect = inset
    monkeypatch.setitem(sys.modules, "AppKit", module)
    return module


@pytest.fixture
def mac(monkeypatch, fake_appkit):
    window = Window()
    monkeypatch.setattr(macos, "_native_window", lambda win_id: window)
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    return adapter, window


def test_mac_backdrop_sits_below_the_content_view_and_is_rounded(mac):
    adapter, window = mac
    assert adapter.apply_backdrop(0x20000, 26, True, 24) is True
    (effect,) = window.frame_view.subviews_
    assert window.frame_view.order == (-1, window.content)
    assert (effect.blending, effect.state, effect.material) == (0, 1, 13)
    assert effect.mask == 2 | 16 and effect.wants_layer
    assert effect.appearance == "appearance:dark"
    assert effect.identifier() == macos.BACKDROP_ID
    assert effect.frame.inset == (24, 24)
    assert (effect.layer().radius, effect.layer().masks, effect.layer().curve) == (
        26,
        True,
        "continuous",
    )
    assert window.opaque is False and window.background == "clear"


def test_mac_backdrop_is_idempotent_and_updates_in_place(mac):
    adapter, window = mac
    for _ in range(3):
        assert adapter.apply_backdrop(0x20000, 26, True, 24)
    assert len(window.frame_view.subviews_) == 1
    first = window.frame_view.subviews_[0]
    assert adapter.apply_backdrop(0x20000, 18, False, 10)
    (effect,) = window.frame_view.subviews_
    assert effect is first
    assert effect.appearance == "appearance:aqua"
    assert effect.layer().radius == 18 and effect.frame.inset == (10, 10)


def test_mac_backdrop_is_removable(mac):
    adapter, window = mac
    assert adapter.clear_backdrop(0x20000) is False
    adapter.apply_backdrop(0x20000, 26, True)
    assert adapter.clear_backdrop(0x20000) is True
    assert window.frame_view.subviews_ == []
    assert adapter.clear_backdrop(0x20000) is False


def test_mac_backdrop_clamps_radius_and_rejects_empty_shapes(mac):
    adapter, window = mac
    assert adapter.apply_backdrop(0x20000, 500, True)
    assert window.frame_view.subviews_[0].layer().radius == 100
    assert adapter.apply_backdrop(0x20000, 26, True, 150) is False
    assert adapter.apply_backdrop(0x20000, 26, True, 400) is False


def test_mac_backdrop_never_raises(monkeypatch, fake_appkit):
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    monkeypatch.setattr(macos, "_native_window", lambda win_id: None)
    assert adapter.apply_backdrop(0x20000, 26, True) is False
    assert adapter.clear_backdrop(0x20000) is False

    monkeypatch.setattr(macos, "_native_window", lambda win_id: Window(with_frame_view=False))
    assert adapter.apply_backdrop(0x20000, 26, True) is False
    assert adapter.clear_backdrop(0x20000) is False

    def boom(win_id):
        raise RuntimeError("objc")

    monkeypatch.setattr(macos, "_native_window", boom)
    assert adapter.apply_backdrop(0x20000, 26, True) is False
    assert adapter.clear_backdrop(0x20000) is False


def test_mac_failed_apply_leaves_no_stale_blur(mac, monkeypatch):
    adapter, window = mac
    assert adapter.apply_backdrop(0x20000, 26, True, 24)
    assert adapter.apply_backdrop(0x20000, 26, True, 400) is False
    assert window.frame_view.subviews_ == []

    assert adapter.apply_backdrop(0x20000, 26, True, 24)

    def boom(self, v):
        raise RuntimeError("layer")

    monkeypatch.setattr(Layer, "setCornerRadius_", boom)
    assert adapter.apply_backdrop(0x20000, 18, False, 24) is False
    assert window.frame_view.subviews_ == []


def test_mac_backdrop_tolerates_non_finite_shapes(mac):
    adapter, window = mac
    nan, inf = float("nan"), float("inf")
    assert adapter.apply_backdrop(0x20000, nan, True, nan)
    effect = window.frame_view.subviews_[0]
    assert effect.layer().radius == 0.0 and effect.frame.inset == (0.0, 0.0)
    assert adapter.apply_backdrop(0x20000, inf, True)
    assert effect.layer().radius == 100
    assert adapter.apply_backdrop(0x20000, 26, True, inf) is False
    assert window.frame_view.subviews_ == []


def test_mac_backdrop_without_pyobjc_returns_false(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    assert adapter.apply_backdrop(0x20000, 26, True) is False


def test_mac_backdrop_ignores_ids_that_cannot_be_view_pointers():
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    for bad in (0, 1, 7, 0xFFFF, 0x20001):
        assert adapter.apply_backdrop(bad, 26, True) is False
        assert adapter.clear_backdrop(bad) is False


def test_mac_adapter_delegates_to_the_module_functions(monkeypatch):
    seen = []
    monkeypatch.setattr(macos, "apply_backdrop", lambda *a: seen.append(("apply", a)) or True)
    monkeypatch.setattr(macos, "clear_backdrop", lambda *a: seen.append(("clear", a)) or True)
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    assert adapter.apply_backdrop(9, 26, True, 24) and adapter.clear_backdrop(9)
    assert seen == [("apply", (9, 26, True, 24)), ("clear", (9,))]


def test_mac_window_methods_ignore_ids_that_cannot_be_view_pointers(monkeypatch):
    touched = []
    fake = types.ModuleType("objc")
    fake.objc_object = lambda **kw: touched.append(kw)
    monkeypatch.setitem(sys.modules, "objc", fake)
    adapter = macos.MacAdapter.__new__(macos.MacAdapter)
    adapter._keep = set()
    for bad in (0, 1, 7, 0xFFFF, 0x20001):
        assert adapter.show_on_all_spaces(bad) is False
        assert adapter.exclude_from_capture(bad) is False
        adapter.include_in_capture(bad)
    assert touched == [] and adapter._keep == set()


def test_mac_corner_curve_is_optional(mac, fake_appkit):
    adapter, window = mac

    class OldLayer(Layer):
        def setCornerCurve_(self, v):
            raise AttributeError

    class OldEffect(Effect):
        def initWithFrame_(self, frame):
            result = super().initWithFrame_(frame)
            self.layer_ = OldLayer()
            return result

    fake_appkit.NSVisualEffectView = OldEffect
    assert adapter.apply_backdrop(0x20000, 26, True)
    assert window.frame_view.subviews_[0].layer().radius == 26


class FakeApi:
    def __init__(self, size=(400, 300), dpi=96, ok=True, accent_ok=True, region_ok=True):
        self.calls = []
        self.size, self.scale_dpi = size, dpi
        self.ok, self.accent_ok, self.region_ok = ok, accent_ok, region_ok

    def dwm_attribute(self, hwnd, attribute, value):
        self.calls.append(("attr", attribute, value))
        return self.ok

    def extend_frame(self, hwnd, margin):
        self.calls.append(("extend", margin))
        return True

    def accent(self, hwnd, state, flags, color):
        self.calls.append(("accent", state, flags, color))
        return self.accent_ok

    def client_size(self, hwnd):
        return self.size

    def dpi(self, hwnd):
        return self.scale_dpi

    def round_region(self, hwnd, box, diameter):
        self.calls.append(("region", box, diameter))
        return self.region_ok

    def clear_region(self, hwnd):
        self.calls.append(("clear_region",))
        return True


@pytest.fixture(autouse=True)
def forget_applied():
    windows._applied.clear()
    yield
    windows._applied.clear()


def test_windows_11_uses_the_system_acrylic_and_clips_to_the_glass():
    api = FakeApi()
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=22631)
    assert ("attr", windows.DWMWA_SYSTEMBACKDROP_TYPE, windows.DWMSBT_TRANSIENTWINDOW) in api.calls
    assert ("attr", windows.DWMWA_WINDOW_CORNER_PREFERENCE, windows.DWMWCP_ROUND) in api.calls
    assert ("attr", windows.DWMWA_USE_IMMERSIVE_DARK_MODE, 1) in api.calls
    assert ("extend", -1) in api.calls
    assert api.calls[-1] == ("region", (24, 24, 377, 277), 52)


def test_windows_11_keeps_the_system_corners_when_they_already_match():
    api = FakeApi()
    assert windows.apply_backdrop(0x1234, 8, False, 0, api=api, build=22621)
    assert ("attr", windows.DWMWA_USE_IMMERSIVE_DARK_MODE, 0) in api.calls
    assert api.calls[-1] == ("clear_region",)
    assert not any(c[0] == "region" for c in api.calls)


def test_windows_region_follows_the_dpi():
    api = FakeApi(size=(600, 450), dpi=144)
    assert windows.apply_backdrop(0x1234, 20, True, 12, api=api, build=22631)
    assert api.calls[-1] == ("region", (18, 18, 583, 433), 60)


def test_windows_10_acrylic_with_a_tint_and_a_round_clip():
    api = FakeApi()
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=19045)
    accent = [c for c in api.calls if c[0] == "accent"]
    assert accent == [("accent", windows.ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, windows._tint(True))]
    assert not any(c[0] == "attr" for c in api.calls)
    assert api.calls[-1][0] == "region"
    assert windows._tint(True) != windows._tint(False)
    assert windows._tint(True) >> 24 == 0x38


def test_windows_10_before_acrylic_uses_plain_blur_behind():
    api = FakeApi()
    assert windows.apply_backdrop(0x1234, 0, False, 0, api=api, build=16299)
    assert api.calls[0][:3] == ("accent", windows.ACCENT_ENABLE_BLURBEHIND, 0)
    assert api.calls[-1] == ("clear_region",)


def test_windows_without_a_blur_returns_false():
    api = FakeApi()
    assert windows.apply_backdrop(0x1234, 26, True, api=api, build=9600) is False
    assert api.calls == []
    assert windows.apply_backdrop(0, 26, True, api=api, build=22631) is False
    assert windows.apply_backdrop(-5, 26, True, api=api, build=22631) is False


def test_windows_failures_return_false():
    assert windows.apply_backdrop(0x1234, 26, True, api=FakeApi(ok=False), build=22631) is False
    assert (
        windows.apply_backdrop(0x1234, 26, True, api=FakeApi(accent_ok=False), build=19045) is False
    )
    assert windows._applied == set()
    tiny = FakeApi(size=(40, 40))
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=tiny, build=22631) is False


@pytest.mark.parametrize(
    "api, build",
    [
        (FakeApi(region_ok=False), 22631),
        (FakeApi(size=None), 22631),
        (FakeApi(size=None), 19045),
    ],
)
def test_windows_clip_failure_removes_the_backdrop_again(api, build):
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=build) is False
    undo = ("attr", windows.DWMWA_SYSTEMBACKDROP_TYPE, windows.DWMSBT_NONE)
    assert (undo in api.calls) == (build >= 22621)
    assert ("clear_region",) in api.calls
    assert windows._applied == set()
    assert windows.clear_backdrop(0x1234, api=api, build=build) is False


def test_windows_never_raises():
    class Exploding:
        def __getattr__(self, name):
            raise OSError(name)

    assert windows.apply_backdrop(0x1234, 26, True, api=Exploding(), build=22631) is False
    assert windows.apply_backdrop(0x1234, 26, True, api=Exploding(), build=19045) is False
    assert windows.apply_backdrop("not a handle", 26, True, api=FakeApi(), build=22631) is False
    assert windows.clear_backdrop("x", api=FakeApi(), build=22631) is False


def test_windows_backdrop_is_idempotent_and_removable():
    api = FakeApi()
    for _ in range(3):
        assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=22631)
    assert windows._applied == {0x1234}
    api.calls.clear()
    assert windows.clear_backdrop(0x1234, api=api, build=22631) is True
    assert ("attr", windows.DWMWA_SYSTEMBACKDROP_TYPE, windows.DWMSBT_NONE) in api.calls
    assert ("extend", 0) in api.calls and ("clear_region",) in api.calls
    assert windows.clear_backdrop(0x1234, api=api, build=22631) is False

    assert windows.apply_backdrop(0x1235, 26, True, api=api, build=19045)
    api.calls.clear()
    assert windows.clear_backdrop(0x1235, api=api, build=19045) is True
    assert api.calls[0] == ("accent", windows.ACCENT_DISABLED, 0, 0)


def test_windows_adapter_methods_delegate_and_the_adapter_stays_unsupported(monkeypatch):
    seen = []
    monkeypatch.setattr(windows, "apply_backdrop", lambda *a: seen.append(("apply", a)) or True)
    monkeypatch.setattr(windows, "clear_backdrop", lambda *a: seen.append(("clear", a)) or True)
    adapter = windows.WindowsAdapter.__new__(windows.WindowsAdapter)
    assert adapter.apply_backdrop(9, 26, True, 24) and adapter.clear_backdrop(9)
    assert seen == [("apply", (9, 26, True, 24)), ("clear", (9,))]
    with pytest.raises(windows.UnsupportedPlatform):
        windows.WindowsAdapter()


@pytest.fixture
def windll(monkeypatch):
    dll = SimpleNamespace(
        dwmapi=SimpleNamespace(
            DwmSetWindowAttribute=MagicMock(return_value=0),
            DwmExtendFrameIntoClientArea=MagicMock(return_value=0),
        ),
        user32=SimpleNamespace(
            SetWindowCompositionAttribute=MagicMock(return_value=1),
            GetClientRect=MagicMock(return_value=1),
            SetWindowRgn=MagicMock(return_value=1),
            GetDpiForWindow=MagicMock(return_value=0),
        ),
        gdi32=SimpleNamespace(
            CreateRoundRectRgn=MagicMock(return_value=77), DeleteObject=MagicMock(return_value=1)
        ),
    )
    loaded = []

    def win_dll(name, *args, **kwargs):
        loaded.append(name)
        return getattr(dll, name)

    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    dll.loaded = loaded
    return dll


def test_windows_api_marshals_the_native_calls(windll):
    api = windows._Api()
    assert api.dwm_attribute(0x1234, 38, 3) is True
    hwnd, attribute, ref, size = windll.dwmapi.DwmSetWindowAttribute.call_args.args
    assert (hwnd, attribute, ref._obj.value, size) == (0x1234, 38, 3, 4)

    assert api.extend_frame(0x1234, -1) is True
    margins = windll.dwmapi.DwmExtendFrameIntoClientArea.call_args.args[1]._obj
    assert (margins.left, margins.right, margins.top, margins.bottom) == (-1, -1, -1, -1)

    captured = {}

    def grab(hwnd, ref):
        data = ref._obj
        policy = windows._AccentPolicy.from_address(data.Data)
        captured.update(attr=data.Attribute, size=data.SizeOfData, policy=policy)
        captured["fields"] = (policy.AccentState, policy.AccentFlags, policy.GradientColor)
        return 1

    windll.user32.SetWindowCompositionAttribute.side_effect = grab
    assert api.accent(0x1234, 4, 2, 0x38241612) is True
    assert captured["attr"] == windows.WCA_ACCENT_POLICY
    assert captured["size"] == ctypes.sizeof(windows._AccentPolicy)
    assert captured["fields"] == (4, 2, 0x38241612)


def test_windows_api_regions_and_sizes(windll):
    api = windows._Api()
    assert api.dpi(1) == 96

    def rect(hwnd, ref):
        ref._obj.right, ref._obj.bottom = 640, 480
        return 1

    windll.user32.GetClientRect.side_effect = rect
    assert api.client_size(1) == (640, 480)
    windll.user32.GetClientRect.side_effect = None
    windll.user32.GetClientRect.return_value = 0
    assert api.client_size(1) is None

    assert api.round_region(1, (1, 2, 3, 4), 20) is True
    windll.gdi32.CreateRoundRectRgn.assert_called_with(1, 2, 3, 4, 20, 20)
    windll.gdi32.DeleteObject.assert_not_called()

    windll.user32.SetWindowRgn.return_value = 0
    assert api.round_region(1, (1, 2, 3, 4), 20) is False
    windll.gdi32.DeleteObject.assert_called_once_with(77)

    windll.gdi32.CreateRoundRectRgn.return_value = 0
    assert api.round_region(1, (1, 2, 3, 4), 20) is False


def test_windows_api_fails_cleanly_where_windll_is_missing(monkeypatch):
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    windows._load.cache_clear()
    assert windows.apply_backdrop(0x1234, 26, True, build=22631) is False
    assert windows._applied == set()


def test_windows_api_uses_private_dlls_and_is_loaded_once(windll):
    windows._load.cache_clear()
    try:
        assert windows._load() is windows._load()
    finally:
        windows._load.cache_clear()
    assert sorted(windll.loaded) == ["dwmapi", "gdi32", "user32"]


def test_windows_backdrop_that_is_refused_undoes_the_frame_extension():
    api = FakeApi(ok=False)
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=22631) is False
    assert ("extend", -1) in api.calls and api.calls[-2:] == [("extend", 0), ("clear_region",)]
    assert windows._applied == set()

    api = FakeApi(accent_ok=False)
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=19045) is False
    assert ("accent", windows.ACCENT_DISABLED, 0, 0) in api.calls
    assert windows._applied == set()


def test_windows_native_call_that_raises_midway_is_undone():
    class Broken(FakeApi):
        def extend_frame(self, hwnd, margin):
            if margin < 0:
                raise OSError("DwmExtendFrameIntoClientArea")
            return super().extend_frame(hwnd, margin)

    api = Broken()
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=22631) is False
    assert ("attr", windows.DWMWA_SYSTEMBACKDROP_TYPE, windows.DWMSBT_NONE) in api.calls
    assert ("extend", 0) in api.calls and windows._applied == set()


def test_windows_clip_that_raises_removes_the_backdrop_again():
    class Broken(FakeApi):
        def client_size(self, hwnd):
            raise OSError("GetClientRect")

    api = Broken()
    assert windows.apply_backdrop(0x1234, 26, True, 24, api=api, build=22631) is False
    assert ("attr", windows.DWMWA_SYSTEMBACKDROP_TYPE, windows.DWMSBT_NONE) in api.calls
    assert windows._applied == set()


def test_windows_rejects_non_finite_shapes_before_touching_the_window():
    api = FakeApi()
    for radius, inset in ((float("nan"), 0), (26, float("inf")), (float("inf"), 0)):
        assert windows.apply_backdrop(0x1234, radius, True, inset, api=api, build=22631) is False
    assert api.calls == [] and windows._applied == set()


def test_windows_clamps_the_corner_to_the_pill_shape_and_ignores_negative_insets():
    api = FakeApi(size=(400, 100))
    assert windows.apply_backdrop(0x1234, 10_000_000, True, -8, api=api, build=22631)
    assert api.calls[-1] == ("region", (0, 0, 401, 101), 100)
