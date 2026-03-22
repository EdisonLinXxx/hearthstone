from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from bot.ocr_config import OcrRegionConfig
from bot.regions import Region
from bot.vision.matcher import crop_region
from bot.vision.scene import SceneDetection


@dataclass(frozen=True)
class HandCard:
    card_id: str
    anchor_center: tuple[int, int]
    drag_start: tuple[int, int]
    playable_score: float
    playable: bool


@dataclass(frozen=True)
class BoardState:
    my_turn: bool
    can_end_turn: bool
    end_turn_active_score: float
    mana_current: int
    mana_total: int
    hand_cards: list[HandCard]


def _playable_score(frame: np.ndarray, anchor_center: tuple[int, int]) -> float:
    x, y = anchor_center
    y1 = max(0, y - 25)
    y2 = min(frame.shape[0], y + 35)
    x1 = max(0, x - 45)
    x2 = min(frame.shape[1], x + 45)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0
    b, g, r = cv2.split(roi)
    green_mask = (g > r + 20) & (g > b + 20) & (g > 120)
    return float(green_mask.mean())


def _detect_playable_cards(hand_image: np.ndarray) -> list[tuple[int, int, int, int]]:
    hsv = cv2.cvtColor(hand_image, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, (35, 90, 110), (95, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
    green_mask = cv2.medianBlur(green_mask, 7)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(green_mask)
    detections: list[tuple[int, int, int, int]] = []
    for index in range(1, num_labels):
        x, y, w, h, area = stats[index].tolist()
        if x < 150 or x > hand_image.shape[1] - 220:
            continue
        if y < 140 or y > hand_image.shape[0] - 25:
            continue
        if area < 700 or h < 28:
            continue
        if w > 130:
            split_count = min(3, max(2, round(w / 80)))
            split_width = w / split_count
            for i in range(split_count):
                sx = int(x + (i * split_width))
                sw = int(split_width)
                detections.append((sx, y, sw, h))
        else:
            detections.append((x, y, w, h))

    merged: list[tuple[int, int, int, int]] = []
    for x, y, w, h in sorted(detections, key=lambda item: item[0]):
        center_x = x + (w // 2)
        if any(abs(center_x - (existing_x + existing_w // 2)) < 42 for existing_x, _, existing_w, _ in merged):
            continue
        merged.append((x, y, w, h))
    if len(merged) > 10:
        merged = merged[:10]
    return merged


def _end_turn_active_score(frame: np.ndarray, end_turn_region: Region) -> float:
    roi = crop_region(frame, end_turn_region)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    yellow_mask = (
        (hsv[:, :, 0] >= 15)
        & (hsv[:, :, 0] <= 45)
        & (hsv[:, :, 1] >= 90)
        & (hsv[:, :, 2] >= 120)
    )
    green_mask = (
        (hsv[:, :, 0] >= 40)
        & (hsv[:, :, 0] <= 95)
        & (hsv[:, :, 1] >= 110)
        & (hsv[:, :, 2] >= 120)
    )
    return max(float(yellow_mask.mean()), float(green_mask.mean()))


def get_end_turn_active_score(frame: np.ndarray, end_turn_region: Region) -> float:
    return _end_turn_active_score(frame, end_turn_region)


def parse_board_state(
    frame: np.ndarray,
    regions: dict[str, Region],
    ocr_config: dict[str, OcrRegionConfig],
    detection: SceneDetection,
    end_turn_threshold: float,
) -> BoardState:
    del ocr_config
    hand_image = crop_region(frame, regions["hand"])
    visible_cards = _detect_playable_cards(hand_image)
    hand_cards: list[HandCard] = []
    for x, y, w, h in visible_cards:
        global_x = regions["hand"].x + x + (w // 2)
        global_y = regions["hand"].y + y + max(10, h // 2)
        drag_start = (
            global_x,
            min(regions["hand"].y + regions["hand"].h - 20, regions["hand"].y + y + h + 35),
        )
        playable_score = _playable_score(frame, (global_x, global_y))
        hand_cards.append(
            HandCard(
                card_id=f"{global_x}:{global_y}",
                anchor_center=(global_x, global_y),
                drag_start=drag_start,
                playable_score=playable_score,
                playable=playable_score >= 0.20,
            )
        )

    end_turn_score = detection.scores.get("end_turn", 0.0)
    active_score = _end_turn_active_score(frame, regions["end_turn"])
    can_end_turn = end_turn_score >= end_turn_threshold and active_score >= 0.018
    return BoardState(
        my_turn=can_end_turn,
        can_end_turn=can_end_turn,
        end_turn_active_score=active_score,
        mana_current=0,
        mana_total=0,
        hand_cards=hand_cards,
    )
