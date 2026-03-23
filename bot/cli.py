from __future__ import annotations

import argparse

from bot.config import RuntimeConfig


SUPPORTED_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "1600x900": (1600, 900),
    "1440x900": (1440, 900),
}


def _build_runtime_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--deck-index",
        type=int,
        required=True,
        help="Deck slot index in the deck selection page, 1-9.",
    )
    parser.add_argument(
        "--resolution",
        choices=tuple(SUPPORTED_RESOLUTIONS),
        default="1600x900",
        help="Window client resolution and asset profile.",
    )
    parser.add_argument(
        "--ocr-auto-sample",
        dest="ocr_auto_sample",
        action="store_true",
        default=None,
        help="Enable automatic OCR sample capture before end turn.",
    )
    parser.add_argument(
        "--no-ocr-auto-sample",
        dest="ocr_auto_sample",
        action="store_false",
        help="Disable automatic OCR sample capture before end turn.",
    )
    return parser


def parse_runtime_args(argv: list[str] | None = None) -> RuntimeConfig:
    parser = _build_runtime_parser()
    args = parser.parse_args(argv)

    if not 1 <= args.deck_index <= 9:
        parser.error("--deck-index must be in the range 1-9.")

    window_width, window_height = SUPPORTED_RESOLUTIONS[args.resolution]

    return RuntimeConfig(
        deck_index=args.deck_index,
        window_width=window_width,
        window_height=window_height,
        asset_profile=args.resolution,
        ocr_auto_sample_enabled=(
            RuntimeConfig(deck_index=args.deck_index).ocr_auto_sample_enabled
            if args.ocr_auto_sample is None
            else bool(args.ocr_auto_sample)
        ),
    )


def parse_args() -> RuntimeConfig:
    parser = argparse.ArgumentParser(
        description="Hearthstone casual-mode automation MVP",
        parents=[_build_runtime_parser()],
    )
    args = parser.parse_args()
    return parse_runtime_args(
        [
            "--deck-index",
            str(args.deck_index),
            "--resolution",
            args.resolution,
            *(["--ocr-auto-sample"] if args.ocr_auto_sample is True else []),
            *(["--no-ocr-auto-sample"] if args.ocr_auto_sample is False else []),
        ]
    )
