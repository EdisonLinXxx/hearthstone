from __future__ import annotations

import random
import time
from typing import Iterable

import pyautogui

from bot.capture import WindowCapture
from bot.regions import Region


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class MouseController:
    def __init__(self, capture: WindowCapture) -> None:
        self.capture = capture

    def _client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        window = self.capture.find_window()
        return window.left + x, window.top + y

    def click_point(self, x: int, y: int, jitter: int = 3, pause_seconds: float = 0.2) -> None:
        dx = random.randint(-jitter, jitter)
        dy = random.randint(-jitter, jitter)
        sx, sy = self._client_to_screen(x + dx, y + dy)
        pyautogui.moveTo(sx, sy, duration=0.10)
        pyautogui.click()
        time.sleep(pause_seconds)

    def click_region(self, region: Region, pause_seconds: float = 0.2) -> None:
        center_x = region.x + (region.w // 2)
        center_y = region.y + (region.h // 2)
        self.click_point(center_x, center_y, pause_seconds=pause_seconds)

    def click_points(self, points: Iterable[tuple[int, int]], pause_seconds: float = 0.2) -> None:
        for x, y in points:
            self.click_point(x, y, pause_seconds=pause_seconds)

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration: float = 0.25) -> None:
        sx, sy = self._client_to_screen(*start)
        ex, ey = self._client_to_screen(*end)
        pyautogui.moveTo(sx, sy, duration=0.10)
        pyautogui.dragTo(ex, ey, duration=duration, button="left")

    def move_to_safe_point(self, x: int, y: int) -> None:
        sx, sy = self._client_to_screen(x, y)
        pyautogui.moveTo(sx, sy, duration=0.10)
