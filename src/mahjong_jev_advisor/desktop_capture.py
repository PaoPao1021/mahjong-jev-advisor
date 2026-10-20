"""Read-only Windows client-window discovery; captures still use the screen."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import sys


@dataclass(frozen=True)
class GameWindow:
    handle: int
    pid: int
    title: str


class WindowUnavailable(ValueError):
    pass


def is_game_title(title: str) -> bool:
    name = title.casefold()
    return any(word in name for word in ("雀魂", "mahjongsoul", "mahjong soul", "じゃんたま")) and not any(
        word in name for word in ("jev", "chrome", "edge", "firefox", "浏览器", "steam")
    )


def _api():
    if sys.platform != "win32":
        raise WindowUnavailable("自动绑定仅支持 Windows；请使用【选择牌桌画面】手动框选 Steam 窗口")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsWindow.restype = wintypes.BOOL
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindowVisible.restype = wintypes.BOOL
    api.IsIconic.argtypes = [wintypes.HWND]
    api.IsIconic.restype = wintypes.BOOL
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.restype = ctypes.c_int
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowTextW.restype = ctypes.c_int
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetClientRect.restype = wintypes.BOOL
    api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    api.ClientToScreen.restype = wintypes.BOOL
    return api


def _title(api, handle) -> str:
    buffer = ctypes.create_unicode_buffer(api.GetWindowTextLengthW(handle) + 1)
    api.GetWindowTextW(handle, buffer, len(buffer))
    return buffer.value


def _pid(api, handle) -> int:
    pid = wintypes.DWORD()
    api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value


def game_windows() -> list[GameWindow]:
    api = _api()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        if api.IsWindowVisible(handle):
            title = _title(api, handle)
            if is_game_title(title):
                windows.append(GameWindow(int(handle), _pid(api, handle), title))
        return True

    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows.restype = wintypes.BOOL
    if not api.EnumWindows(visit, 0):
        raise WindowUnavailable("无法读取桌面窗口列表，请手动框选")
    return windows


def client_box(window: GameWindow) -> tuple[int, int, int, int]:
    api = _api()
    handle = window.handle
    if not api.IsWindow(handle) or _pid(api, handle) != window.pid or not is_game_title(_title(api, handle)):
        raise WindowUnavailable("已绑定的雀魂窗口已关闭或改变，请重新绑定")
    if not api.IsWindowVisible(handle) or api.IsIconic(handle):
        raise WindowUnavailable("雀魂窗口不可见或已最小化，请恢复游戏窗口")
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if not api.GetClientRect(handle, ctypes.byref(rect)) or not api.ClientToScreen(handle, ctypes.byref(origin)):
        raise WindowUnavailable("无法取得雀魂窗口位置，请重新绑定或手动框选")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise WindowUnavailable("雀魂窗口大小无效")
    return origin.x, origin.y, width, height
