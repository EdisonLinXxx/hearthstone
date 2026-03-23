from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from bot.ocr_config import OcrRegionConfig
from bot.regions import HandDetectionConfig, Region
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


@dataclass(frozen=True)
class HandCandidate:
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    area: int
    aspect_ratio: float
    green_ratio: float
    band_score: float


@dataclass(frozen=True)
class ScoredHandCandidate:
    candidate: HandCandidate
    card_score: float
    playable_score: float
    center_green_ratio: float
    rim_green_ratio: float
    local_brightness: float


def _normalize_kernel_size(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def _clip_roi(
    width: int,
    height: int,
    center: tuple[int, int],
    half_width: int,
    up: int,
    down: int,
) -> tuple[int, int, int, int] | None:
    x, y = center
    x1 = max(0, x - half_width)
    x2 = min(width, x + half_width)
    y1 = max(0, y - up)
    y2 = min(height, y + down)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _ratio_or_zero(mask: np.ndarray) -> float:
    if mask.size == 0:
        return 0.0
    return float(mask.mean())


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _build_hand_green_mask(hand_image: np.ndarray, config: HandDetectionConfig) -> np.ndarray:
    hsv = cv2.cvtColor(hand_image, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, config.hsv_lower, config.hsv_upper)
    kernel_size = _normalize_kernel_size(max(1, config.close_kernel))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
    blur_size = _normalize_kernel_size(max(1, config.median_blur))
    green_mask = cv2.medianBlur(green_mask, blur_size)
    return green_mask


def _collect_raw_hand_candidates(
    hand_image: np.ndarray,
    green_mask: np.ndarray,
    config: HandDetectionConfig,
) -> list[HandCandidate]:
    height, width = hand_image.shape[:2]
    min_x = int(width * config.valid_x_min_ratio)
    max_x = int(width * config.valid_x_max_ratio)
    min_y = int(height * config.valid_y_min_ratio)
    max_y = int(height * config.valid_y_max_ratio)
    min_area = int(width * height * config.min_area_ratio)
    max_area = int(width * height * config.max_area_ratio)
    min_width = int(width * config.min_width_ratio)
    max_width = int(width * config.max_width_ratio)
    min_height = int(height * config.min_height_ratio)
    max_height = int(height * config.max_height_ratio)
    wide_blob_min_width = int(width * config.wide_blob_min_width_ratio)
    min_bottom = int(height * config.valid_bottom_min_ratio)
    blacklist_left_max = int(width * config.blacklist_left_max_ratio)
    blacklist_bottom_min = int(height * config.blacklist_bottom_min_ratio)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(green_mask)
    candidates: list[HandCandidate] = []
    for index in range(1, num_labels):
        x, y, w, h, area = (int(value) for value in stats[index].tolist())
        center = (x + (w // 2), y + (h // 2))
        bottom = y + h
        if center[0] < min_x or center[0] > max_x:
            continue
        if center[1] < min_y or center[1] > max_y:
            continue
        if h < min_height or h > max_height:
            continue
        if bottom < min_bottom:
            continue
        if center[0] <= blacklist_left_max and bottom >= blacklist_bottom_min:
            continue
        aspect_ratio = (w / h) if h else 0.0
        if area >= min_area and w >= min_width and w <= max_width:
            if aspect_ratio >= config.min_aspect_ratio and aspect_ratio <= config.max_aspect_ratio:
                candidates.append(_build_hand_candidate(green_mask, x, y, w, h, area))
                continue
        if w >= wide_blob_min_width and aspect_ratio > config.max_aspect_ratio:
            candidates.extend(
                _split_wide_hand_blob(
                    green_mask=green_mask,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    config=config,
                    hand_width=width,
                    min_area=min_area,
                    min_width=min_width,
                    max_width=max_width,
                    min_height=min_height,
                    max_area=max_area,
                )
            )
    return candidates


def _build_hand_candidate(
    green_mask: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    area: int,
) -> HandCandidate:
    center = (x + (w // 2), y + (h // 2))
    aspect_ratio = (w / h) if h else 0.0
    roi = green_mask[y : y + h, x : x + w]
    green_ratio = _ratio_or_zero(roi > 0)
    band_top = y + max(0, int(h * 0.15))
    band_bottom = y + max(1, int(h * 0.55))
    band_bottom = min(green_mask.shape[0], band_bottom)
    band_roi = green_mask[band_top:band_bottom, x : x + w]
    band_score = _ratio_or_zero(band_roi > 0)
    return HandCandidate(
        bbox=(x, y, w, h),
        center=center,
        area=area,
        aspect_ratio=aspect_ratio,
        green_ratio=green_ratio,
        band_score=band_score,
    )


def _extract_peak_positions(
    profile: np.ndarray,
    min_distance: int,
    threshold_ratio: float,
) -> list[int]:
    if profile.size == 0:
        return []
    window = _normalize_kernel_size(max(3, min_distance // 2))
    kernel = np.ones(window, dtype=np.float32) / float(window)
    smooth = np.convolve(profile.astype(np.float32), kernel, mode="same")
    peak_threshold = float(smooth.max()) * threshold_ratio
    if peak_threshold <= 0.0:
        return []
    peak_indices: list[int] = []
    for index in range(1, len(smooth) - 1):
        value = float(smooth[index])
        if value < peak_threshold:
            continue
        if value >= float(smooth[index - 1]) and value >= float(smooth[index + 1]):
            peak_indices.append(index)
    if not peak_indices:
        peak_indices = [int(np.argmax(smooth))]
    ranked = sorted(peak_indices, key=lambda idx: float(smooth[idx]), reverse=True)
    chosen: list[int] = []
    for index in ranked:
        if any(abs(index - existing) < min_distance for existing in chosen):
            continue
        chosen.append(index)
    return sorted(chosen)


def _split_wide_hand_blob(
    green_mask: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    config: HandDetectionConfig,
    hand_width: int,
    min_area: int,
    min_width: int,
    max_width: int,
    min_height: int,
    max_area: int,
) -> list[HandCandidate]:
    roi = (green_mask[y : y + h, x : x + w] > 0).astype(np.uint8)
    if roi.size == 0:
        return []
    column_profile = roi.mean(axis=0)
    min_distance = max(1, int(hand_width * config.wide_blob_peak_min_distance_ratio))
    peaks = _extract_peak_positions(
        profile=column_profile,
        min_distance=min_distance,
        threshold_ratio=config.wide_blob_peak_threshold_ratio,
    )
    if len(peaks) <= 1:
        return []
    boundaries = [0]
    for left_peak, right_peak in zip(peaks, peaks[1:]):
        boundaries.append((left_peak + right_peak) // 2)
    boundaries.append(w)

    split_candidates: list[HandCandidate] = []
    for index, peak in enumerate(peaks):
        local_left = boundaries[index]
        local_right = boundaries[index + 1]
        segment = roi[:, local_left:local_right]
        if segment.size == 0:
            continue
        rows, cols = np.where(segment > 0)
        if len(rows) == 0 or len(cols) == 0:
            continue
        seg_x1 = x + local_left + int(cols.min())
        seg_x2 = x + local_left + int(cols.max()) + 1
        seg_y1 = y + int(rows.min())
        seg_y2 = y + int(rows.max()) + 1
        seg_w = seg_x2 - seg_x1
        seg_h = seg_y2 - seg_y1
        seg_area = int(segment.sum())
        if seg_w < max(6, min_width // 2):
            continue
        if seg_h < min_height:
            continue
        if seg_w > max_width:
            continue
        if seg_area < max(20, min_area // 2):
            continue
        if seg_area > max_area:
            continue
        split_candidates.append(
            _build_hand_candidate(
                green_mask=green_mask,
                x=seg_x1,
                y=seg_y1,
                w=seg_w,
                h=seg_h,
                area=seg_area,
            )
        )
    return split_candidates


def _candidate_priority(candidate: HandCandidate) -> tuple[float, float, int]:
    return (
        candidate.band_score,
        candidate.green_ratio,
        candidate.area,
    )


def _dedupe_hand_candidates(
    candidates: list[HandCandidate],
    config: HandDetectionConfig,
    hand_width: int,
) -> list[HandCandidate]:
    min_distance = max(1, int(hand_width * config.dedupe_min_center_distance_ratio))
    deduped: list[HandCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.center[0]):
        if not deduped:
            deduped.append(candidate)
            continue
        previous = deduped[-1]
        previous_left = previous.bbox[0]
        previous_right = previous.bbox[0] + previous.bbox[2]
        candidate_left = candidate.bbox[0]
        candidate_right = candidate.bbox[0] + candidate.bbox[2]
        overlap = max(0, min(previous_right, candidate_right) - max(previous_left, candidate_left))
        min_width = max(1, min(previous.bbox[2], candidate.bbox[2]))
        overlap_ratio = overlap / min_width
        contains_candidate = previous_left <= candidate_left and previous_right >= candidate_right
        candidate_contains_previous = candidate_left <= previous_left and candidate_right >= previous_right
        adaptive_distance = min(
            min_distance,
            max(12, int(min_width * 0.55)),
        )
        should_merge_nested = (
            abs(candidate.center[0] - previous.center[0]) < min_distance
            and overlap_ratio >= 0.85
            and (contains_candidate or candidate_contains_previous)
        )
        should_merge_dense_overlap = (
            abs(candidate.center[0] - previous.center[0]) < adaptive_distance
            and overlap_ratio >= 0.35
        )
        if should_merge_nested or should_merge_dense_overlap:
            if _candidate_priority(candidate) > _candidate_priority(previous):
                deduped[-1] = candidate
            continue
        deduped.append(candidate)
    return deduped[: config.max_cards]


def _probe_green_ratio(
    hsv_image: np.ndarray,
    center: tuple[int, int],
    half_width: int,
    up: int,
    down: int,
    config: HandDetectionConfig,
) -> float:
    roi = _clip_roi(hsv_image.shape[1], hsv_image.shape[0], center, half_width, up, down)
    if roi is None:
        return 0.0
    x1, y1, x2, y2 = roi
    hsv_roi = hsv_image[y1:y2, x1:x2]
    if hsv_roi.size == 0:
        return 0.0
    green_mask = cv2.inRange(hsv_roi, config.hsv_lower, config.hsv_upper) > 0
    return _ratio_or_zero(green_mask)


def _probe_brightness(
    frame: np.ndarray,
    center: tuple[int, int],
    half_width: int,
    up: int,
    down: int,
) -> float:
    roi = _clip_roi(frame.shape[1], frame.shape[0], center, half_width, up, down)
    if roi is None:
        return 0.0
    x1, y1, x2, y2 = roi
    patch = frame[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0
    hsv_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    return float(hsv_patch[:, :, 2].mean() / 255.0)


def _score_playable_candidate(
    hand_image: np.ndarray,
    candidate: HandCandidate,
    config: HandDetectionConfig,
) -> ScoredHandCandidate:
    height, width = hand_image.shape[:2]
    hsv_image = cv2.cvtColor(hand_image, cv2.COLOR_BGR2HSV)
    center = candidate.center

    center_green_ratio = _probe_green_ratio(
        hsv_image,
        center,
        max(2, int(width * config.center_probe_half_width_ratio)),
        max(1, int(height * config.center_probe_up_ratio)),
        max(1, int(height * config.center_probe_down_ratio)),
        config,
    )
    rim_center = (
        center[0],
        max(0, center[1] - int(candidate.bbox[3] * 0.25)),
    )
    rim_green_ratio = _probe_green_ratio(
        hsv_image,
        rim_center,
        max(2, int(width * config.rim_probe_half_width_ratio)),
        max(1, int(height * config.rim_probe_top_ratio)),
        max(1, int(height * config.rim_probe_height_ratio)),
        config,
    )
    local_brightness = _probe_brightness(
        hand_image,
        center,
        max(2, int(width * config.brightness_probe_half_width_ratio)),
        max(1, int(height * config.brightness_probe_up_ratio)),
        max(1, int(height * config.brightness_probe_down_ratio)),
    )
    _, bbox_y, _, bbox_h = candidate.bbox
    bottom_ratio = (bbox_y + bbox_h) / float(max(1, height))
    height_ratio = bbox_h / float(max(1, height))
    bottom_score = _clamp01(
        (bottom_ratio - config.valid_bottom_min_ratio)
        / max(0.05, 1.0 - config.valid_bottom_min_ratio),
    )
    height_mid = (config.min_height_ratio + config.max_height_ratio) / 2.0
    height_span = max(0.04, (config.max_height_ratio - config.min_height_ratio) / 2.0)
    height_score = _clamp01(1.0 - (abs(height_ratio - height_mid) / height_span))
    card_score = (
        (candidate.band_score * config.card_band_weight)
        + (candidate.green_ratio * config.card_green_weight)
        + (bottom_score * config.card_bottom_weight)
        + (height_score * config.card_height_weight)
    )
    playable_score = (
        (center_green_ratio * config.playable_center_weight)
        + (rim_green_ratio * config.playable_rim_weight)
        + (local_brightness * config.playable_brightness_weight)
    )
    return ScoredHandCandidate(
        candidate=candidate,
        card_score=card_score,
        playable_score=playable_score,
        center_green_ratio=center_green_ratio,
        rim_green_ratio=rim_green_ratio,
        local_brightness=local_brightness,
    )


def _candidate_to_hand_card(
    hand_region: Region,
    scored_candidate: ScoredHandCandidate,
    config: HandDetectionConfig,
) -> HandCard:
    x, y = scored_candidate.candidate.center
    global_x = hand_region.x + x
    global_y = hand_region.y + y
    _, bbox_y, _, bbox_h = scored_candidate.candidate.bbox
    anchor_offset = max(10, int(bbox_h * config.drag_anchor_from_bottom_ratio))
    drag_y = hand_region.y + bbox_y + bbox_h - anchor_offset
    drag_start = (
        global_x,
        max(hand_region.y + 8, min(hand_region.y + hand_region.h - 20, drag_y)),
    )
    return HandCard(
        card_id=f"{global_x}:{global_y}",
        anchor_center=(global_x, global_y),
        drag_start=drag_start,
        playable_score=scored_candidate.playable_score,
        playable=(
            scored_candidate.card_score >= config.card_threshold
            and scored_candidate.playable_score >= config.playable_threshold
        ),
    )


def build_hand_debug_entries(
    frame: np.ndarray,
    hand_region: Region,
    hand_config: HandDetectionConfig,
) -> list[dict[str, object]]:
    hand_image = crop_region(frame, hand_region)
    green_mask = _build_hand_green_mask(hand_image, hand_config)
    raw_candidates = _collect_raw_hand_candidates(hand_image, green_mask, hand_config)
    deduped_candidates = _dedupe_hand_candidates(raw_candidates, hand_config, hand_region.w)
    debug_entries: list[dict[str, object]] = []
    for candidate in deduped_candidates:
        scored = _score_playable_candidate(hand_image, candidate, hand_config)
        debug_entries.append(
            {
                "center": candidate.center,
                "bbox": candidate.bbox,
                "area": candidate.area,
                "aspect_ratio": round(candidate.aspect_ratio, 3),
                "green_ratio": round(candidate.green_ratio, 3),
                "band_score": round(candidate.band_score, 3),
                "card_score": round(scored.card_score, 3),
                "center_green_ratio": round(scored.center_green_ratio, 3),
                "rim_green_ratio": round(scored.rim_green_ratio, 3),
                "local_brightness": round(scored.local_brightness, 3),
                "playable_score": round(scored.playable_score, 3),
                "playable": (
                    scored.card_score >= hand_config.card_threshold
                    and scored.playable_score >= hand_config.playable_threshold
                ),
            }
        )
    return debug_entries


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
    hand_config: HandDetectionConfig,
) -> BoardState:
    del ocr_config
    hand_region = regions["hand"]
    hand_image = crop_region(frame, hand_region)
    green_mask = _build_hand_green_mask(hand_image, hand_config)
    raw_candidates = _collect_raw_hand_candidates(hand_image, green_mask, hand_config)
    candidates = _dedupe_hand_candidates(raw_candidates, hand_config, hand_region.w)
    scored_candidates = [
        _score_playable_candidate(hand_image, candidate, hand_config)
        for candidate in candidates
    ]
    hand_cards = [
        _candidate_to_hand_card(hand_region, scored_candidate, hand_config)
        for scored_candidate in scored_candidates
    ]

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
