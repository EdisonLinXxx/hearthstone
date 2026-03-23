from __future__ import annotations

from dataclasses import dataclass

from bot.loader import load_yaml


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class HandDetectionConfig:
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    close_kernel: int
    median_blur: int
    valid_x_min_ratio: float
    valid_x_max_ratio: float
    valid_y_min_ratio: float
    valid_y_max_ratio: float
    min_area_ratio: float
    max_area_ratio: float
    min_width_ratio: float
    max_width_ratio: float
    min_height_ratio: float
    max_height_ratio: float
    valid_bottom_min_ratio: float
    min_aspect_ratio: float
    max_aspect_ratio: float
    dedupe_min_center_distance_ratio: float
    wide_blob_min_width_ratio: float
    wide_blob_peak_min_distance_ratio: float
    wide_blob_peak_threshold_ratio: float
    max_cards: int
    blacklist_left_max_ratio: float
    blacklist_bottom_min_ratio: float
    center_probe_half_width_ratio: float
    center_probe_up_ratio: float
    center_probe_down_ratio: float
    rim_probe_half_width_ratio: float
    rim_probe_top_ratio: float
    rim_probe_height_ratio: float
    brightness_probe_half_width_ratio: float
    brightness_probe_up_ratio: float
    brightness_probe_down_ratio: float
    playable_center_weight: float
    playable_rim_weight: float
    playable_brightness_weight: float
    card_band_weight: float
    card_green_weight: float
    card_bottom_weight: float
    card_height_weight: float
    card_threshold: float
    playable_threshold: float
    drag_anchor_from_bottom_ratio: float


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


def load_hand_detection_config(path) -> HandDetectionConfig:
    data = load_yaml(path)
    raw = data.get("hand_detection", {})
    if not raw:
        raise KeyError("Missing 'hand_detection' section in regions config.")
    return HandDetectionConfig(
        hsv_lower=tuple(int(value) for value in raw["hsv_lower"]),
        hsv_upper=tuple(int(value) for value in raw["hsv_upper"]),
        close_kernel=int(raw["close_kernel"]),
        median_blur=int(raw["median_blur"]),
        valid_x_min_ratio=float(raw["valid_x_min_ratio"]),
        valid_x_max_ratio=float(raw["valid_x_max_ratio"]),
        valid_y_min_ratio=float(raw["valid_y_min_ratio"]),
        valid_y_max_ratio=float(raw["valid_y_max_ratio"]),
        min_area_ratio=float(raw["min_area_ratio"]),
        max_area_ratio=float(raw["max_area_ratio"]),
        min_width_ratio=float(raw["min_width_ratio"]),
        max_width_ratio=float(raw["max_width_ratio"]),
        min_height_ratio=float(raw["min_height_ratio"]),
        max_height_ratio=float(raw["max_height_ratio"]),
        valid_bottom_min_ratio=float(raw.get("valid_bottom_min_ratio", 0.60)),
        min_aspect_ratio=float(raw["min_aspect_ratio"]),
        max_aspect_ratio=float(raw["max_aspect_ratio"]),
        dedupe_min_center_distance_ratio=float(raw["dedupe_min_center_distance_ratio"]),
        wide_blob_min_width_ratio=float(raw["wide_blob_min_width_ratio"]),
        wide_blob_peak_min_distance_ratio=float(raw["wide_blob_peak_min_distance_ratio"]),
        wide_blob_peak_threshold_ratio=float(raw["wide_blob_peak_threshold_ratio"]),
        max_cards=int(raw["max_cards"]),
        blacklist_left_max_ratio=float(raw.get("blacklist_left_max_ratio", 0.0)),
        blacklist_bottom_min_ratio=float(raw.get("blacklist_bottom_min_ratio", 1.0)),
        center_probe_half_width_ratio=float(raw["center_probe_half_width_ratio"]),
        center_probe_up_ratio=float(raw["center_probe_up_ratio"]),
        center_probe_down_ratio=float(raw["center_probe_down_ratio"]),
        rim_probe_half_width_ratio=float(raw["rim_probe_half_width_ratio"]),
        rim_probe_top_ratio=float(raw["rim_probe_top_ratio"]),
        rim_probe_height_ratio=float(raw["rim_probe_height_ratio"]),
        brightness_probe_half_width_ratio=float(raw["brightness_probe_half_width_ratio"]),
        brightness_probe_up_ratio=float(raw["brightness_probe_up_ratio"]),
        brightness_probe_down_ratio=float(raw["brightness_probe_down_ratio"]),
        playable_center_weight=float(raw["playable_center_weight"]),
        playable_rim_weight=float(raw["playable_rim_weight"]),
        playable_brightness_weight=float(raw["playable_brightness_weight"]),
        card_band_weight=float(raw.get("card_band_weight", 0.35)),
        card_green_weight=float(raw.get("card_green_weight", 0.20)),
        card_bottom_weight=float(raw.get("card_bottom_weight", 0.30)),
        card_height_weight=float(raw.get("card_height_weight", 0.15)),
        card_threshold=float(raw.get("card_threshold", 0.28)),
        playable_threshold=float(raw["playable_threshold"]),
        drag_anchor_from_bottom_ratio=float(raw.get("drag_anchor_from_bottom_ratio", 0.28)),
    )
