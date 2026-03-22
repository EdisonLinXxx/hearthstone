from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from bot.capture import WindowCapture
from bot.config import RuntimeConfig, SAMPLES_DIR
from bot.regions import load_regions


class SampleCollector:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.capture = WindowCapture(config)
        self.regions = load_regions(config.regions_path)

    def _base_output_dir(self, tag: str) -> Path:
        safe_tag = tag.strip().replace(" ", "_")
        output_dir = SAMPLES_DIR / self.config.asset_profile / safe_tag
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def collect(self, tag: str, include_regions: bool) -> list[Path]:
        self.capture.move_window()
        window = self.capture.find_window()
        self.capture.validate_window(window)
        frame = self.capture.capture_window()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self._base_output_dir(tag)

        saved_paths: list[Path] = []
        full_path = output_dir / f"{timestamp}_full.png"
        self.capture.to_pil_image(frame).save(full_path)
        saved_paths.append(full_path)

        if include_regions:
            for name, region in self.regions.items():
                region_frame = self.capture.crop_region(frame, region)
                region_path = output_dir / f"{timestamp}_{name}.png"
                self.capture.to_pil_image(region_frame).save(region_path)
                saved_paths.append(region_path)

        metadata_path = output_dir / f"{timestamp}_meta.txt"
        metadata_path.write_text(
            "\n".join(
                [
                    f"tag={tag}",
                    f"title={window.title}",
                    f"left={window.left}",
                    f"top={window.top}",
                    f"width={window.width}",
                    f"height={window.height}",
                    f"profile={self.config.asset_profile}",
                ]
            ),
            encoding="utf-8",
        )
        saved_paths.append(metadata_path)
        logger.info("Saved {} sample files to {}", len(saved_paths), output_dir)
        return saved_paths
