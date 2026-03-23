from __future__ import annotations

import argparse
from pathlib import Path

from bot.ocr_labeler_app import run_ocr_labeler


DEFAULT_CSV = Path("bot/datasets/ocr/1440x900/mana_to_label.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="标注 full 界面当前法力值/总法力值 OCR 数据集 CSV。")
    parser.add_argument(
        "--csv",
        default=str(DEFAULT_CSV),
        help="当前法力值/总法力值标注 CSV 清单路径。",
    )
    args = parser.parse_args()
    return run_ocr_labeler(Path(args.csv), label_mode="mana")


if __name__ == "__main__":
    raise SystemExit(main())
