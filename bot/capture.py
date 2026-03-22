from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any

import mss
import numpy as np
import pygetwindow as gw
from PIL import Image
from loguru import logger

from bot.config import RuntimeConfig


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32 = ctypes.windll.user32


@dataclass(frozen=True)
class WindowInfo:
    title: str
    hwnd: int
    left: int
    top: int
    width: int
    height: int
    outer_left: int
    outer_top: int
    outer_width: int
    outer_height: int


class WindowCapture:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._sct = mss.mss()

    def _list_candidate_titles(self) -> list[str]:
        titles = []
        for title in gw.getAllTitles():
            stripped = title.strip()
            if stripped:
                titles.append(stripped)
        return titles

    def _select_window(self):
        exact_candidates = []
        partial_candidates = []
        for alias in self.config.window_titles:
            windows = gw.getWindowsWithTitle(alias)
            for window in windows:
                title = getattr(window, "title", "").strip()
                if not title:
                    continue
                if title == alias:
                    exact_candidates.append(window)
                elif alias.lower() in title.lower():
                    partial_candidates.append(window)
        candidates = exact_candidates or partial_candidates
        if not candidates:
            visible_titles = self._list_candidate_titles()
            raise RuntimeError(
                "Could not find a matching game window. "
                f"Expected one of {self.config.window_titles}. "
                f"Visible titles: {visible_titles[:20]}",
            )
        selected = sorted(candidates, key=lambda item: len(getattr(item, "title", "")))[0]
        logger.debug("Selected window title: {}", getattr(selected, "title", ""))
        return selected

    def _get_window_metrics(self, hwnd: int) -> tuple[RECT, RECT, POINT]:
        window_rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            raise RuntimeError("GetWindowRect failed.")

        client_rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            raise RuntimeError("GetClientRect failed.")

        client_origin = POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
            raise RuntimeError("ClientToScreen failed.")

        return window_rect, client_rect, client_origin

    def find_window(self) -> WindowInfo:
        window = self._select_window()
        hwnd = int(window._hWnd)
        window_rect, client_rect, client_origin = self._get_window_metrics(hwnd)
        client_width = int(client_rect.right - client_rect.left)
        client_height = int(client_rect.bottom - client_rect.top)
        outer_width = int(window_rect.right - window_rect.left)
        outer_height = int(window_rect.bottom - window_rect.top)
        return WindowInfo(
            title=window.title,
            hwnd=hwnd,
            left=int(client_origin.x),
            top=int(client_origin.y),
            width=client_width,
            height=client_height,
            outer_left=int(window_rect.left),
            outer_top=int(window_rect.top),
            outer_width=outer_width,
            outer_height=outer_height,
        )

    def move_window(self, _window: WindowInfo | None = None) -> WindowInfo:
        window = self._select_window()
        if window.isMinimized:
            window.restore()

        current = self.find_window()
        left_border = current.left - current.outer_left
        top_border = current.top - current.outer_top
        right_border = (current.outer_left + current.outer_width) - (current.left + current.width)
        bottom_border = (current.outer_top + current.outer_height) - (current.top + current.height)

        target_outer_left = self.config.window_x - left_border
        target_outer_top = self.config.window_y - top_border
        target_outer_width = self.config.window_width + left_border + right_border
        target_outer_height = self.config.window_height + top_border + bottom_border

        window.moveTo(target_outer_left, target_outer_top)
        window.resizeTo(target_outer_width, target_outer_height)
        return self.find_window()

    def validate_window(self, window: WindowInfo) -> None:
        if window.width != self.config.window_width or window.height != self.config.window_height:
            raise RuntimeError(
                "Unexpected client size: "
                f"{window.width}x{window.height}, "
                f"expected {self.config.window_width}x{self.config.window_height}. "
                f"Window title: {window.title}. "
                f"Outer size: {window.outer_width}x{window.outer_height}.",
            )

    def capture_window(self) -> np.ndarray:
        window = self.find_window()
        self.validate_window(window)
        raw = self._sct.grab(
            {
                "left": window.left,
                "top": window.top,
                "width": window.width,
                "height": window.height,
            },
        )
        frame = np.array(raw)
        return frame[:, :, :3]

    def crop_region(self, frame: np.ndarray, region: Any) -> np.ndarray:
        return frame[region.y : region.y + region.h, region.x : region.x + region.w].copy()

    @staticmethod
    def to_pil_image(frame: np.ndarray) -> Image.Image:
        rgb_frame = frame[:, :, ::-1]
        return Image.fromarray(rgb_frame)
