from __future__ import annotations

import argparse
from pathlib import Path

from bot.ocr_labeler_app import run_ocr_labeler


DEFAULT_CSV = Path("bot/datasets/ocr/1440x900/mana_to_label.csv")


def detect_label_mode(csv_path: Path) -> str:
    return "cost" if "cost" in csv_path.name.lower() else "mana"


def main() -> int:
    parser = argparse.ArgumentParser(description="标注 OCR 数据集 CSV。")
    parser.add_argument(
        "--csv",
        default=str(DEFAULT_CSV),
        help="OCR 标注 CSV 清单路径。",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    return run_ocr_labeler(csv_path, label_mode=detect_label_mode(csv_path))


if __name__ == "__main__":
    raise SystemExit(main())
