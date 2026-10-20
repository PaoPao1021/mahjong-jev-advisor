import ctypes
import os
import sys
from ctypes import wintypes

import pytest

from mahjong_jev_advisor import desktop_capture as capture
from mahjong_jev_advisor.settings import Settings


def test_capture_profiles_roundtrip_and_template_isolation():
    browser_box = (-100, 20, 1000, 700)
    browser_regions = {"hand": (0.1, 0.8, 0.7, 0.1)}
    settings = Settings(game_box=browser_box, regions=browser_regions, api_key="keep-key")
    browser_templates = settings.template_dir()
    settings.switch_input_source("steam")
    assert settings.game_box is None and settings.regions == {}
    assert settings.template_dir() != browser_templates
    settings.game_box = (50, 60, 1200, 900)
    settings.regions = {"hand": (0.2, 0.8, 0.6, 0.1)}
    settings.save()
    restored = Settings.load()
    assert restored.input_source == "steam" and restored.api_key == "keep-key"
    restored.switch_input_source("browser")
    assert restored.game_box == browser_box and restored.regions == browser_regions
    assert restored.template_dir() == browser_templates
    restored.switch_input_source("steam")
    assert restored.game_box == settings.game_box and restored.regions == settings.regions


@pytest.mark.parametrize("fields", [
    {"input_source": "bad"}, {"input_source": []},
    {"capture_profiles": []}, {"capture_profiles": {"bad": {}}},
    {"capture_profiles": {"steam": {"game_box": [0, 0, -1, 100]}}},
    {"capture_profiles": {"steam": {"capture_profiles": {}}}},
])
def test_invalid_capture_configuration_rejected(fields):
    with pytest.raises((ValueError, TypeError)):
        Settings._validate_data(fields)


@pytest.mark.parametrize("title, expected", [
    ("雀魂麻将", True), ("MahjongSoul", True), ("Mahjong Soul", True), ("じゃんたま", True),
    ("雀魂 · Jev 实时切牌顾问", False), ("雀魂 - Google Chrome", False),
    ("Mahjong Soul - Microsoft Edge", False), ("MahjongSoul on Steam", False),
    ("Other Game", False),
])
def test_window_title_filter(title, expected):
    assert capture.is_game_title(title) is expected


class FakeWindows:
    exists = True
    visible = True
    minimized = False
    pid = 42
    title = "雀魂麻将"
    origin = (-900, 35)
    size = (800, 600)

    def IsWindow(self, handle): return self.exists
    def IsWindowVisible(self, handle): return self.visible
    def IsIconic(self, handle): return self.minimized
    def GetWindowThreadProcessId(self, handle, pointer): pointer._obj.value = self.pid
    def GetWindowTextLengthW(self, handle): return len(self.title)
    def GetWindowTextW(self, handle, buffer, count): buffer.value = self.title
    def GetClientRect(self, handle, pointer):
        pointer._obj.right, pointer._obj.bottom = self.size
        return True
    def ClientToScreen(self, handle, pointer):
        pointer._obj.x, pointer._obj.y = self.origin
        return True


def test_native_client_box_uses_screen_origin_and_tracks_moves(monkeypatch):
    api = FakeWindows()
    monkeypatch.setattr(capture, "_api", lambda: api)
    window = capture.GameWindow(2**40, 42, api.title)
    assert capture.client_box(window) == (-900, 35, 800, 600)
    api.origin = (100, 50)
    assert capture.client_box(window) == (100, 50, 800, 600)


@pytest.mark.parametrize("field, value", [
    ("exists", False), ("visible", False), ("minimized", True),
    ("pid", 43), ("title", "unrelated window"), ("size", (0, 0)),
])
def test_native_client_box_rejects_unavailable_or_reused_window(monkeypatch, field, value):
    api = FakeWindows()
    setattr(api, field, value)
    monkeypatch.setattr(capture, "_api", lambda: api)
    with pytest.raises(capture.WindowUnavailable):
        capture.client_box(capture.GameWindow(123, 42, "雀魂麻将"))


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises real Win32 window handles")
def test_real_windows_discovery_move_resize_and_close():
    api = capture._api()
    api.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    api.CreateWindowExW.restype = wintypes.HWND
    api.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    api.MoveWindow.restype = wintypes.BOOL
    api.DestroyWindow.argtypes = [wintypes.HWND]
    api.DestroyWindow.restype = wintypes.BOOL
    # Native visible popup: no Qt/offscreen dependency and no game installation needed.
    handle = api.CreateWindowExW(0, "STATIC", "MahjongSoul capture test", 0x90000000,
                                50, 60, 320, 240, None, None, None, None)
    assert handle
    window = capture.GameWindow(int(handle), os.getpid(), "MahjongSoul capture test")
    try:
        assert window in capture.game_windows()
        assert capture.client_box(window) == (50, 60, 320, 240)
        assert api.MoveWindow(handle, 100, 110, 400, 300, True)
        assert capture.client_box(window) == (100, 110, 400, 300)
    finally:
        api.DestroyWindow(handle)
    with pytest.raises(capture.WindowUnavailable):
        capture.client_box(window)
