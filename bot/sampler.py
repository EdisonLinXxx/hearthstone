from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from bot.capture import WindowCapture
from bot.capture import WindowInfo
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
        return self.collect_from_frame(
            tag=tag,
            frame=frame,
            window=window,
            include_regions=include_regions,
        )

    def collect_from_frame(
        self,
        tag: str,
        frame,
        window: WindowInfo,
        include_regions: bool,
        region_names: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> list[Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = self._base_output_dir(tag)

        saved_paths: list[Path] = []
        full_path = output_dir / f"{timestamp}_full.png"
        self.capture.to_pil_image(frame).save(full_path)
        saved_paths.append(full_path)

        if include_regions:
            selected_region_names = region_names or list(self.regions)
            for name in selected_region_names:
                region = self.regions.get(name)
                if region is None:
                    continue
                region_frame = self.capture.crop_region(frame, region)
                region_path = output_dir / f"{timestamp}_{name}.png"
                self.capture.to_pil_image(region_frame).save(region_path)
                saved_paths.append(region_path)

        metadata_lines = [
            f"tag={tag}",
            f"title={window.title}",
            f"left={window.left}",
            f"top={window.top}",
            f"width={window.width}",
            f"height={window.height}",
            f"profile={self.config.asset_profile}",
        ]
        for key, value in (metadata or {}).items():
            metadata_lines.append(f"{key}={value}")

        metadata_path = output_dir / f"{timestamp}_meta.txt"
        metadata_path.write_text("\n".join(metadata_lines), encoding="utf-8")
        saved_paths.append(metadata_path)
        logger.info("Saved {} sample files to {}", len(saved_paths), output_dir)
        return saved_paths
