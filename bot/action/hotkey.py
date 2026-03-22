from __future__ import annotations

import threading

import keyboard


class HotkeyController:
    def __init__(self) -> None:
        self._stop_requested = threading.Event()
        self._registered = False
        self._hotkey: str | None = None

    def register_stop_hotkey(self, hotkey: str) -> None:
        if self._registered and self._hotkey == hotkey:
            return
        if self._registered:
            keyboard.unhook_all_hotkeys()
            self._registered = False
        keyboard.add_hotkey(hotkey, self.request_stop)
        self._registered = True
        self._hotkey = hotkey

    def request_stop(self) -> None:
        self._stop_requested.set()

    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def clear(self) -> None:
        self._stop_requested.clear()

    def cleanup(self) -> None:
        if self._registered:
            keyboard.unhook_all_hotkeys()
            self._registered = False
