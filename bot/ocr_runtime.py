from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.config import BOT_DIR
from bot.ocr_config import OcrRegionConfig


@dataclass(frozen=True)
class OcrTemplateSample:
    label: str
    image: np.ndarray


class DatasetOcr:
    def __init__(self, asset_profile: str, ocr_config: dict[str, OcrRegionConfig]) -> None:
        self.asset_profile = asset_profile
        self.ocr_config = ocr_config
        self.base_dir = BOT_DIR.parent
        self.dataset_dir = BOT_DIR / "datasets" / "ocr" / asset_profile
        self.mana_size = (120, 28)
        self.cost_size = (44, 36)
        self.mana_samples = self._load_samples("mana", self.dataset_dir / "mana_to_label.csv", self.mana_size)
        self.cost_samples = self._load_samples("cost", self.dataset_dir / "cost_to_label.csv", self.cost_size)

    def recognize_mana(self, image: np.ndarray) -> tuple[str | None, float]:
        return self._match_label("mana", image, self.mana_samples, self.mana_size)

    def recognize_cost(self, image: np.ndarray) -> tuple[str | None, float]:
        return self._match_label("cost", image, self.cost_samples, self.cost_size)

    def _get_region_config(self, region_name: str) -> OcrRegionConfig | None:
        config = self.ocr_config.get(region_name)
        if config is not None:
            return config
        if region_name == "cost":
            return self.ocr_config.get("mana")
        return None

    def _load_samples(
        self,
        region_name: str,
        csv_path: Path,
        output_size: tuple[int, int],
    ) -> list[OcrTemplateSample]:
        if not csv_path.exists():
            return []
        config = self._get_region_config(region_name)
        if config is None:
            return []

        samples: list[OcrTemplateSample] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                label = row.get("label", "").strip()
                if row.get("label_status") != "done" or not label:
                    continue
                image_path = self._resolve_dataset_path(row.get("image_path", ""))
                if image_path is None:
                    continue
                image = cv2.imread(str(image_path))
                if image is None:
                    continue
                samples.append(
                    OcrTemplateSample(
                        label=label,
                        image=self._preprocess(image, config, output_size),
                    )
                )
        return samples

    def _match_label(
        self,
        region_name: str,
        image: np.ndarray,
        samples: list[OcrTemplateSample],
        output_size: tuple[int, int],
    ) -> tuple[str | None, float]:
        if not samples:
            return None, 0.0
        config = self._get_region_config(region_name)
        if config is None:
            return None, 0.0

        processed = self._preprocess(image, config, output_size)
        ranked: list[tuple[float, str]] = []
        for sample in samples:
            diff = np.mean(np.abs(processed.astype(np.float32) - sample.image.astype(np.float32))) / 255.0
            ranked.append((float(diff), sample.label))
        ranked.sort(key=lambda item: item[0])

        best_diff, best_label = ranked[0]
        second_diff = ranked[1][0] if len(ranked) > 1 else 1.0
        confidence = float(max(0.0, 1.0 - best_diff))
        if second_diff > best_diff:
            confidence += float(min(0.25, (second_diff - best_diff) * 0.5))
        return best_label, min(1.0, confidence)

    def _resolve_dataset_path(self, raw_path: str) -> Path | None:
        value = raw_path.strip()
        if not value:
            return None

        path = Path(value)
        if path.is_absolute():
            return path
        return self.base_dir / path

    def _preprocess(
        self,
        image: np.ndarray,
        config: OcrRegionConfig,
        output_size: tuple[int, int],
    ) -> np.ndarray:
        working = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
        if config.scale > 1:
            working = cv2.resize(
                working,
                None,
                fx=config.scale,
                fy=config.scale,
                interpolation=cv2.INTER_CUBIC,
            )

        _, binary = cv2.threshold(working, config.threshold, 255, cv2.THRESH_BINARY)
        if config.invert:
            binary = cv2.bitwise_not(binary)

        points = cv2.findNonZero(binary)
        if points is not None:
            x, y, w, h = cv2.boundingRect(points)
            binary = binary[y : y + h, x : x + w]

        canvas_width, canvas_height = output_size
        if binary.size == 0:
            return np.zeros((canvas_height, canvas_width), dtype=np.uint8)

        src_height, src_width = binary.shape[:2]
        scale = min(canvas_width / max(1, src_width), canvas_height / max(1, src_height))
        resized = cv2.resize(
            binary,
            (
                max(1, int(src_width * scale)),
                max(1, int(src_height * scale)),
            ),
            interpolation=cv2.INTER_AREA,
        )
        canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
        offset_x = (canvas_width - resized.shape[1]) // 2
        offset_y = (canvas_height - resized.shape[0]) // 2
        canvas[offset_y : offset_y + resized.shape[0], offset_x : offset_x + resized.shape[1]] = resized
        return canvas
