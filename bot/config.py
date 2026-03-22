from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
BOT_DIR = BASE_DIR / "bot"
REGIONS_DIR = BOT_DIR / "regions"
TEMPLATES_DIR = BOT_DIR / "templates"
OCR_DIR = BOT_DIR / "ocr"
LOGS_DIR = BOT_DIR / "logs"
SAMPLES_DIR = BOT_DIR / "samples"

DEFAULT_WINDOW_WIDTH = 1600
DEFAULT_WINDOW_HEIGHT = 900
DEFAULT_WINDOW_MODE = "windowed"
DEFAULT_DPI_SCALE = 1.0
DEFAULT_WINDOW_TITLE = "炉石传说"
DEFAULT_WINDOW_TITLES = ("炉石传说", "Hearthstone")
DEFAULT_LANGUAGE = "zh_CN"
DEFAULT_MODE = "casual"
DEFAULT_STOP_HOTKEY = "F8"
DEFAULT_WINDOW_X = 20
DEFAULT_WINDOW_Y = 20
DEFAULT_ASSET_PROFILE = "1600x900"
DEFAULT_MOUSE_PARK_X = 120
DEFAULT_MOUSE_PARK_Y = 120
DEFAULT_STAGNANT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class RuntimeConfig:
    deck_index: int
    window_width: int = DEFAULT_WINDOW_WIDTH
    window_height: int = DEFAULT_WINDOW_HEIGHT
    window_mode: str = DEFAULT_WINDOW_MODE
    dpi_scale: float = DEFAULT_DPI_SCALE
    window_title: str = DEFAULT_WINDOW_TITLE
    window_titles: tuple[str, ...] = DEFAULT_WINDOW_TITLES
    language: str = DEFAULT_LANGUAGE
    mode: str = DEFAULT_MODE
    stop_hotkey: str = DEFAULT_STOP_HOTKEY
    window_x: int = DEFAULT_WINDOW_X
    window_y: int = DEFAULT_WINDOW_Y
    asset_profile: str = DEFAULT_ASSET_PROFILE
    mouse_park_x: int = DEFAULT_MOUSE_PARK_X
    mouse_park_y: int = DEFAULT_MOUSE_PARK_Y
    poll_interval_seconds: float = 0.30
    max_result_clicks: int = 4
    max_actions_per_turn: int = 8
    stagnant_timeout_seconds: float = DEFAULT_STAGNANT_TIMEOUT_SECONDS

    @property
    def regions_path(self) -> Path:
        return REGIONS_DIR / f"{self.asset_profile}.yaml"

    @property
    def templates_dir(self) -> Path:
        return TEMPLATES_DIR / self.asset_profile

    @property
    def templates_index_path(self) -> Path:
        return self.templates_dir / "templates.yaml"

    @property
    def ocr_config_path(self) -> Path:
        return OCR_DIR / f"{self.asset_profile}.yaml"
