from __future__ import annotations

from dataclasses import dataclass

from bot.loader import load_yaml


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int


def load_regions(path) -> dict[str, Region]:
    data = load_yaml(path)
    raw_regions = data.get("regions", {})
    regions: dict[str, Region] = {}
    for name, value in raw_regions.items():
        regions[name] = Region(
            x=int(value["x"]),
            y=int(value["y"]),
            w=int(value["w"]),
            h=int(value["h"]),
        )
    return regions


def load_deck_slots(path) -> dict[int, tuple[int, int]]:
    data = load_yaml(path)
    raw_slots = data.get("deck_slots", {})
    slots: dict[int, tuple[int, int]] = {}
    for key, value in raw_slots.items():
        slots[int(key)] = (int(value["x"]), int(value["y"]))
    return slots
