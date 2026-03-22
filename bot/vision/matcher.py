from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from bot.regions import Region
from bot.template_index import TemplateSpec


@dataclass(frozen=True)
class MatchResult:
    score: float
    x: int
    y: int
    width: int
    height: int


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@lru_cache(maxsize=64)
def load_template_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Template image not found: {path}")
    return image


def crop_region(frame: np.ndarray, region: Region) -> np.ndarray:
    return frame[region.y : region.y + region.h, region.x : region.x + region.w].copy()


def match_template(frame: np.ndarray, region: Region, spec: TemplateSpec) -> MatchResult:
    search_image = _to_gray(crop_region(frame, region))
    template = load_template_image(str(Path(spec.path)))
    if search_image.shape[0] < template.shape[0] or search_image.shape[1] < template.shape[1]:
        return MatchResult(score=0.0, x=region.x, y=region.y, width=template.shape[1], height=template.shape[0])

    result = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return MatchResult(
        score=float(max_val),
        x=region.x + int(max_loc[0]),
        y=region.y + int(max_loc[1]),
        width=int(template.shape[1]),
        height=int(template.shape[0]),
    )
