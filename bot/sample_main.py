from __future__ import annotations

import argparse

from bot.cli import parse_runtime_args
from bot.logging_utils import setup_logging
from bot.sampler import SampleCollector


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture tagged Hearthstone samples for template and OCR tuning.",
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Scene tag, for example: main_menu, battle_menu, deck_select, in_battle, result.",
    )
    parser.add_argument(
        "--no-regions",
        action="store_true",
        help="Only save the full window image.",
    )
    args, remaining = parser.parse_known_args()

    config = parse_runtime_args(remaining)
    setup_logging()
    collector = SampleCollector(config)
    collector.collect(tag=args.tag, include_regions=not args.no_regions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
