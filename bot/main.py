from __future__ import annotations

from bot.cli import parse_args
from bot.logging_utils import setup_logging
from bot.runtime import HearthstoneBot


def main() -> int:
    config = parse_args()
    setup_logging()
    bot = HearthstoneBot(config)
    return bot.run()


if __name__ == "__main__":
    raise SystemExit(main())
