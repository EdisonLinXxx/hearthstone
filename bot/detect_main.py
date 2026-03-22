from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from bot.cli import parse_runtime_args
from bot.logging_utils import setup_logging
from bot.regions import load_regions
from bot.template_index import load_template_specs
from bot.vision.scene import detect_scene


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run scene detection on a saved sample image.",
    )
    parser.add_argument("--image", required=True, help="Path to a full-window sample PNG.")
    args, remaining = parser.parse_known_args()

    config = parse_runtime_args(remaining)
    setup_logging()

    frame = cv2.imread(args.image)
    if frame is None:
        raise FileNotFoundError(f"Could not read image: {args.image}")

    regions = load_regions(config.regions_path)
    specs = load_template_specs(config.templates_index_path, config.templates_dir)
    detection = detect_scene(frame, regions, specs)

    print(f"scene={detection.scene}")
    for name, score in detection.scores.items():
        print(f"{name}={score:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
