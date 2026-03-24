from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

import cv2
import numpy as np
from loguru import logger

from bot.action.hotkey import HotkeyController
from bot.action.mouse import MouseController
from bot.capture import WindowCapture
from bot.config import RuntimeConfig
from bot.ocr_config import load_ocr_config
from bot.ocr_runtime import DatasetOcr
from bot.regions import load_deck_slots, load_hand_detection_config, load_regions
from bot.sampler import SampleCollector
from bot.strategy.rules import decide_action
from bot.template_index import load_template_specs
from bot.vision.board_state import (
    BoardState,
    HandCard,
    build_hand_debug_entries,
    get_end_turn_active_score,
    parse_board_state,
)
from bot.vision.matcher import crop_region
from bot.vision.scene import SceneDetection, detect_scene


@dataclass(frozen=True)
class GemValidationMetrics:
    blue_ratio: float
    white_ratio: float
    circularity: float
    fill_ratio: float
    edge_density: float
    score: float


@dataclass(frozen=True)
class GemCandidate:
    center_x: int
    center_y: int
    radius: int
    bbox: tuple[int, int, int, int]
    metrics: GemValidationMetrics
    source: str


class HearthstoneBot:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.capture = WindowCapture(config)
        self.regions = load_regions(config.regions_path)
        self.deck_slots = load_deck_slots(config.regions_path)
        self.hand_detection_config = load_hand_detection_config(config.regions_path)
        self.templates = load_template_specs(
            config.templates_index_path,
            config.templates_dir,
        )
        self.ocr_config = load_ocr_config(config.ocr_config_path)
        self.dataset_ocr = DatasetOcr(config.asset_profile, self.ocr_config)
        self.sampler = SampleCollector(config)
        self.mouse = MouseController(self.capture)
        self.hotkeys = HotkeyController()
        self._last_scene: str | None = None
        self._battle_logged = False
        self._queue_step = 0
        self._pending_traditional_battle = False
        self._last_battle_signature: tuple[object, ...] | None = None
        self._turn_action_count = 0
        self._attempted_cards_this_turn: set[str] = set()
        self._last_play_attempt_card_id: str | None = None
        self._result_click_count = 0
        self._last_queue_action_at = 0.0
        self._battle_stall_count = 0
        self._mulligan_grace_until = 0.0
        self._unknown_result_suspect_count = 0
        self._unknown_since = 0.0
        self._last_battle_seen_at = 0.0
        self._last_frame_signature: np.ndarray | None = None
        self._last_frame_change_at = 0.0
        self._last_progress_at = 0.0
        self._end_turn_ready_at = 0.0
        self._end_turn_confirm_count = 0
        self._last_ocr_sample_signature: tuple[int, tuple[str, ...]] | None = None
        self._last_trusted_mana_state: tuple[int, int, bool] | None = None
        self._last_anomaly_sample_signature: tuple[str, str, int, int] | None = None
        self._last_anomaly_sample_at = 0.0

    def _board_play_target(self) -> tuple[int, int]:
        return (
            self.config.window_width // 2,
            int(self.config.window_height * 0.48),
        )

    def _frame_signature(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (48, 27), interpolation=cv2.INTER_AREA)
        return resized

    def _refresh_frame_change_timer(self, frame: np.ndarray, now: float) -> None:
        signature = self._frame_signature(frame)
        if self._last_frame_signature is None:
            self._last_frame_signature = signature
            self._last_frame_change_at = now
            return
        diff = cv2.absdiff(signature, self._last_frame_signature)
        if float(diff.mean()) >= 2.0:
            self._last_frame_change_at = now
            self._last_frame_signature = signature

    def _handle_stagnant_screen(self, now: float) -> bool:
        if self._last_frame_change_at == 0.0:
            return False
        if now - self._last_frame_change_at < self.config.stagnant_timeout_seconds:
            return False
        logger.warning(
            "Screen looks unchanged for {}s. Fallback click at safe point ({}, {}).",
            round(now - self._last_frame_change_at, 1),
            self.config.mouse_park_x,
            self.config.mouse_park_y,
        )
        self.mouse.click_point(
            self.config.mouse_park_x,
            self.config.mouse_park_y,
            pause_seconds=0.6,
        )
        self._park_mouse()
        self._last_frame_change_at = now
        return True

    def _mark_progress(self, now: float) -> None:
        self._last_progress_at = now

    def _log_scene_normalization(
        self,
        from_scene: str,
        to_scene: str,
        reason: str,
        detection: SceneDetection,
    ) -> None:
        if from_scene == to_scene:
            return
        logger.info(
            "Scene normalized: {} -> {} ({}) scores={{confirm: {}, result_banner: {}, result_continue_text: {}, end_turn: {}, back_button: {}, queue_play_button: {}}}",
            from_scene,
            to_scene,
            reason,
            round(detection.scores.get("confirm", 0.0), 3),
            round(detection.scores.get("result_banner", 0.0), 3),
            round(detection.scores.get("result_continue_text", 0.0), 3),
            round(detection.scores.get("end_turn", 0.0), 3),
            round(detection.scores.get("back_button", 0.0), 3),
            round(detection.scores.get("queue_play_button", 0.0), 3),
        )

    def _has_recent_battle_context(self, now: float, timeout_seconds: float = 60.0) -> bool:
        return self._last_battle_seen_at > 0.0 and (now - self._last_battle_seen_at) < timeout_seconds

    def _has_active_queue_context(self, now: float, timeout_seconds: float = 45.0) -> bool:
        return (
            self._queue_step >= 1
            and self._last_queue_action_at > 0.0
            and (now - self._last_queue_action_at) < timeout_seconds
        )

    def _looks_like_result_overlay(
        self,
        detection: SceneDetection,
        board_state: BoardState | None,
        now: float,
    ) -> bool:
        if detection.scene in {"result", "result_continue"}:
            return True
        if detection.scene not in {"unknown", "battle", "confirm_dialog"}:
            return False
        if board_state is None:
            return False
        if board_state.end_turn_active_score >= 0.01:
            return False
        if len(board_state.hand_cards) > 0:
            return False
        if not self._has_recent_battle_context(now):
            return False
        back_button_score = detection.scores.get("back_button", 0.0)
        queue_play_score = detection.scores.get("queue_play_button", 0.0)
        main_battle_score = detection.scores.get("main_battle_button", 0.0)
        traditional_battle_score = detection.scores.get("traditional_battle_button", 0.0)
        if back_button_score >= 0.86:
            return False
        if queue_play_score >= 0.12:
            return False
        if main_battle_score >= 0.70:
            return False
        if traditional_battle_score >= 0.70:
            return False
        return (
            detection.scores.get("result_banner", 0.0) >= 0.10
            or detection.scores.get("result_continue_text", 0.0) >= 0.10
            or detection.scores.get("confirm", 0.0) >= 0.22
        )

    def _looks_like_match_error(self, detection: SceneDetection, now: float) -> bool:
        if detection.scores.get("result_banner", 0.0) >= 0.35:
            return False
        if detection.scores.get("result_continue_text", 0.0) >= 0.35:
            return False
        confirm_score = detection.scores.get("confirm", 0.0)
        back_button_score = detection.scores.get("back_button", 0.0)
        queue_play_score = detection.scores.get("queue_play_button", 0.0)
        main_battle_score = detection.scores.get("main_battle_button", 0.0)
        traditional_battle_score = detection.scores.get("traditional_battle_button", 0.0)
        casual_mode_score = detection.scores.get("casual_mode_button", 0.0)
        has_active_queue_context = self._has_active_queue_context(now)
        strong_confirm_match = confirm_score >= self.templates["confirm"].threshold
        menu_like_context = (
            back_button_score >= 0.80
            and (
                queue_play_score >= 0.10
                or main_battle_score >= 0.60
                or traditional_battle_score >= 0.60
                or casual_mode_score >= 0.60
            )
        )
        strong_menu_error_match = strong_confirm_match and menu_like_context
        relaxed_menu_error_match = (
            confirm_score >= 0.14
            and back_button_score >= 0.85
            and (
                queue_play_score >= 0.12
                or main_battle_score >= 0.65
                or traditional_battle_score >= 0.65
                or casual_mode_score >= 0.65
            )
        )
        return (has_active_queue_context and strong_confirm_match) or strong_menu_error_match or relaxed_menu_error_match

    def _match_error_reason(self, detection: SceneDetection) -> str:
        confirm_score = detection.scores.get("confirm", 0.0)
        back_button_score = detection.scores.get("back_button", 0.0)
        queue_play_score = detection.scores.get("queue_play_button", 0.0)
        main_battle_score = detection.scores.get("main_battle_button", 0.0)
        traditional_battle_score = detection.scores.get("traditional_battle_button", 0.0)
        casual_mode_score = detection.scores.get("casual_mode_button", 0.0)
        menu_like_context = (
            back_button_score >= 0.80
            and (
                queue_play_score >= 0.10
                or main_battle_score >= 0.60
                or traditional_battle_score >= 0.60
                or casual_mode_score >= 0.60
            )
        )
        if confirm_score >= self.templates["confirm"].threshold and menu_like_context:
            return "strong menu confirm"
        if confirm_score >= self.templates["confirm"].threshold:
            return "strong queue confirm"
        if confirm_score >= 0.14 and back_button_score >= 0.85:
            return "relaxed menu confirm"
        return "confirm dialog in menu context"

    def _looks_like_result_confirm(self, detection: SceneDetection, now: float) -> bool:
        if detection.scores.get("confirm", 0.0) < self.templates["confirm"].threshold:
            return False
        if self._looks_like_match_error(detection, now):
            return False
        if self._has_active_queue_context(now):
            return False
        if self._has_recent_battle_context(now, timeout_seconds=120.0):
            return True
        return (
            self._result_click_count > 0
            or detection.scores.get("result_banner", 0.0) >= 0.05
            or detection.scores.get("result_continue_text", 0.0) >= 0.05
        )

    def _should_promote_unknown_to_battle(
        self,
        detection: SceneDetection,
        frame: np.ndarray,
    ) -> bool:
        return (
            detection.scene == "unknown"
            and detection.scores.get("end_turn", 0.0) >= 0.78
            and detection.scores.get("result_banner", 0.0) < 0.35
            and detection.scores.get("result_continue_text", 0.0) < 0.35
            and monotonic() >= self._mulligan_grace_until
            and get_end_turn_active_score(frame, self.regions["end_turn"]) >= 0.015
        )

    def _normalize_scene_pre_board(
        self,
        detection: SceneDetection,
        frame: np.ndarray,
        now: float,
    ) -> SceneDetection:
        if self._should_promote_unknown_to_battle(detection, frame):
            normalized = SceneDetection(
                scene="battle",
                scores=detection.scores,
                matches=detection.matches,
            )
            self._log_scene_normalization(detection.scene, normalized.scene, "unknown promoted by end_turn active", detection)
            return normalized
        if detection.scene == "confirm_dialog" and self._looks_like_match_error(detection, now):
            normalized = SceneDetection(
                scene="match_error",
                scores=detection.scores,
                matches=detection.matches,
            )
            self._log_scene_normalization(
                detection.scene,
                normalized.scene,
                self._match_error_reason(detection),
                detection,
            )
            return normalized
        return detection

    def _normalize_scene_post_board(
        self,
        detection: SceneDetection,
        now: float,
        board_state: BoardState | None,
    ) -> SceneDetection:
        if detection.scene == "confirm_dialog":
            if self._looks_like_result_confirm(detection, now):
                normalized = SceneDetection(
                    scene="result",
                    scores=detection.scores,
                    matches=detection.matches,
                )
                self._log_scene_normalization(detection.scene, normalized.scene, "result confirm dialog", detection)
                return normalized
            return detection
        if detection.scene in {"unknown", "battle"} and self._looks_like_result_overlay(detection, board_state, now):
            normalized = SceneDetection(
                scene="result_continue",
                scores=detection.scores,
                matches=detection.matches,
            )
            self._log_scene_normalization(detection.scene, normalized.scene, "result overlay after battle", detection)
            return normalized
        return detection

    def _handle_stagnant_progress(
        self,
        now: float,
        detection: SceneDetection,
        board_state: BoardState | None,
    ) -> bool:
        if self._last_progress_at == 0.0:
            self._last_progress_at = now
            return False
        stagnant_for = now - self._last_progress_at
        if stagnant_for < self.config.stagnant_timeout_seconds:
            return False
        if self._looks_like_result_overlay(detection, board_state, now):
            logger.warning(
                "No progress for {}s and screen looks like result. Fallback click result continue button.",
                round(stagnant_for, 1),
            )
            self.mouse.click_region(self.regions["result_continue_button"], pause_seconds=1.0)
            self._park_mouse()
            self._last_progress_at = now
            return True
        logger.warning(
            "No progress for {}s. Fallback click at safe point ({}, {}).",
            round(stagnant_for, 1),
            self.config.mouse_park_x,
            self.config.mouse_park_y,
        )
        self.mouse.click_point(
            self.config.mouse_park_x,
            self.config.mouse_park_y,
            pause_seconds=0.6,
        )
        self._park_mouse()
        self._last_progress_at = now
        return True

    def run(self) -> int:
        logger.info(
            "Starting bot with deck_index={}, profile={}, window={}x{} @ ({}, {}), mode={}, hotkey={}",
            self.config.deck_index,
            self.config.asset_profile,
            self.config.window_width,
            self.config.window_height,
            self.config.window_x,
            self.config.window_y,
            self.config.mode,
            self.config.stop_hotkey,
        )
        logger.info(
            "Loaded {} regions, {} templates, {} OCR configs.",
            len(self.regions),
            len(self.templates),
            len(self.ocr_config),
        )
        try:
            self.hotkeys.clear()
            self.hotkeys.register_stop_hotkey(self.config.stop_hotkey)
            window = self.capture.move_window()
            self.capture.validate_window(window)
            self._last_progress_at = monotonic()
            logger.info(
                "Window ready: '{}' at ({}, {}) size={}x{}",
                window.title,
                window.left,
                window.top,
                window.width,
                window.height,
            )
        except Exception as exc:
            logger.error("Window setup failed: {}", exc)
            return 1

        logger.info("Stop hotkey registered: {}", self.config.stop_hotkey)
        logger.info("Runtime flow is partially implemented: startup/menu/queue/result click flow is active.")

        try:
            while not self.hotkeys.stop_requested():
                now = monotonic()
                frame = self.capture.capture_window()
                self._refresh_frame_change_timer(frame, now)
                if self._handle_stagnant_screen(now):
                    time.sleep(self.config.poll_interval_seconds)
                    continue
                detection = detect_scene(frame, self.regions, self.templates)
                if detection.scene == "unknown":
                    if self._unknown_since == 0.0:
                        self._unknown_since = now
                else:
                    self._unknown_since = 0.0
                if detection.scene == "battle":
                    self._last_battle_seen_at = now
                detection = self._normalize_scene_pre_board(detection, frame, now)

                if self._pending_traditional_battle:
                    logger.info("Pending step: click traditional battle entry.")
                    self._click_match(
                        detection,
                        "traditional_battle_button",
                        fallback_region="traditional_battle_button",
                        pause_seconds=1.0,
                    )
                    self._pending_traditional_battle = False
                    self._queue_step = 1
                    time.sleep(self.config.poll_interval_seconds)
                    continue

                board_state = None
                unknown_board_state = None
                end_turn_active_score = get_end_turn_active_score(frame, self.regions["end_turn"])
                if detection.scene == "unknown" and now >= self._mulligan_grace_until:
                    unknown_board_state = parse_board_state(
                        frame=frame,
                        regions=self.regions,
                        ocr_config=self.ocr_config,
                        detection=detection,
                        end_turn_threshold=self.templates["end_turn"].threshold,
                        hand_config=self.hand_detection_config,
                    )
                    looks_like_result = (
                        end_turn_active_score < 0.01
                        and len(unknown_board_state.hand_cards) == 0
                        and (now - self._last_battle_seen_at) < 20.0
                        and (
                            detection.scores.get("result_banner", 0.0) >= 0.22
                            or detection.scores.get("result_continue_text", 0.0) >= 0.18
                        )
                    )
                    if looks_like_result:
                        self._unknown_result_suspect_count += 1
                    else:
                        self._unknown_result_suspect_count = 0
                elif detection.scene == "unknown":
                    self._unknown_result_suspect_count = 0
                if detection.scene == "battle":
                    board_state = parse_board_state(
                        frame=frame,
                        regions=self.regions,
                        ocr_config=self.ocr_config,
                        detection=detection,
                        end_turn_threshold=self.templates["end_turn"].threshold,
                        hand_config=self.hand_detection_config,
                    )
                    hand_debug_entries = build_hand_debug_entries(
                        frame,
                        self.regions["hand"],
                        self.hand_detection_config,
                    )
                    board_state = self._apply_ocr_board_state(
                        frame,
                        board_state,
                        hand_debug_entries=hand_debug_entries,
                    )
                    if (
                        not board_state.can_end_turn
                        and len(board_state.hand_cards) == 0
                        and (
                            detection.scores.get("result_banner", 0.0) >= 0.12
                            or detection.scores.get("result_continue_text", 0.0) >= 0.20
                        )
                    ):
                        logger.info(
                            "Battle scene looks like result screen. Force continue. banner_score={}, continue_score={}, end_turn_active_score={}",
                            round(detection.scores.get("result_banner", 0.0), 3),
                            round(detection.scores.get("result_continue_text", 0.0), 3),
                            round(board_state.end_turn_active_score, 3),
                        )
                        self.mouse.click_region(self.regions["result_continue_button"], pause_seconds=1.0)
                        self._park_mouse()
                        self._battle_logged = False
                        self._last_battle_signature = None
                        self._turn_action_count = 0
                        self._attempted_cards_this_turn.clear()
                        self._last_play_attempt_card_id = None
                        self._battle_stall_count = 0
                        time.sleep(self.config.poll_interval_seconds)
                        continue
                    signature = (
                        board_state.hand_source,
                        board_state.hand_cards_ready,
                        len(board_state.hand_cards),
                        tuple(card.card_id for card in board_state.hand_cards),
                    )
                    if signature != self._last_battle_signature:
                        if self._last_play_attempt_card_id and self._last_battle_signature is not None:
                            self._attempted_cards_this_turn.discard(self._last_play_attempt_card_id)
                        self._turn_action_count = 0
                        self._last_battle_signature = signature
                        self._battle_stall_count = 0
                    elif self._last_play_attempt_card_id:
                        self._attempted_cards_this_turn.add(self._last_play_attempt_card_id)
                        self._last_play_attempt_card_id = None
                        self._battle_stall_count += 1
                    elif board_state.hand_cards_ready and any(card.playable for card in board_state.hand_cards):
                        self._battle_stall_count += 1
                    logger.info(
                        "Battle decision state: source={} ready={} trusted={} reject_reasons={} final_cards={}, mana={}/{}, end_turn_active_score={}, stall_count={}, hand_cards={}, attempted={}",
                        board_state.hand_source,
                        board_state.hand_cards_ready,
                        board_state.ocr_trusted,
                        list(board_state.ocr_reject_reasons[:3]),
                        len(board_state.hand_cards),
                        board_state.mana_current,
                        board_state.mana_total,
                        round(board_state.end_turn_active_score, 3),
                        self._battle_stall_count,
                        [
                            {
                                "id": card.card_id,
                                "cost": card.mana_cost,
                                "ocr_conf": round(card.ocr_confidence, 3),
                                "playable": card.playable,
                            }
                            for card in board_state.hand_cards
                        ],
                        sorted(self._attempted_cards_this_turn),
                    )
                    logger.debug(
                        "Battle debug-only legacy hand candidates (not used for decision): {}",
                        hand_debug_entries,
                    )
                    self._collect_battle_anomaly_sample(
                        frame=frame,
                        scene=detection.scene,
                        board_state=board_state,
                        hand_debug_entries=hand_debug_entries,
                        now=now,
                    )
                    if board_state.can_end_turn and now >= self._end_turn_ready_at:
                        self._end_turn_confirm_count += 1
                    else:
                        self._end_turn_confirm_count = 0
                detection = self._normalize_scene_post_board(
                    detection=detection,
                    now=now,
                    board_state=board_state or unknown_board_state,
                )
                if detection.scene == "battle":
                    self._last_battle_seen_at = now
                if detection.scene != self._last_scene:
                    logger.info("Current scene: {}", detection.scene)
                    logger.info(
                        "Scene scores: {}",
                        {k: round(v, 3) for k, v in detection.scores.items()},
                    )
                    self._mark_progress(now)
                    self._last_scene = detection.scene
                    if detection.scene not in {"result", "result_continue"}:
                        self._result_click_count = 0
                    if detection.scene not in {"mulligan", "unknown"}:
                        self._mulligan_grace_until = 0.0
                    if detection.scene != "unknown":
                        self._unknown_result_suspect_count = 0
                        self._unknown_since = 0.0
                    if detection.scene != "battle":
                        self._battle_logged = False
                        self._last_battle_signature = None
                        self._turn_action_count = 0
                        self._attempted_cards_this_turn.clear()
                        self._last_play_attempt_card_id = None
                        self._battle_stall_count = 0
                        self._end_turn_ready_at = 0.0
                        self._end_turn_confirm_count = 0
                        self._last_ocr_sample_signature = None
                        self._last_trusted_mana_state = None
                        self._last_anomaly_sample_signature = None
                        self._last_anomaly_sample_at = 0.0
                    if detection.scene in {"main_menu", "battle_menu", "queue_page", "matching"}:
                        self._last_battle_seen_at = 0.0
                    if detection.scene not in {"queue_page", "matching", "match_error"}:
                        self._last_queue_action_at = 0.0
                elif detection.scene == "unknown" and self._unknown_result_suspect_count >= 3:
                    logger.info(
                        "Unknown scene looks like result screen. Force continue. banner_score={}, continue_score={}, suspect_count={}, unknown_for={}s, last_battle={}s, detected_cards={}",
                        round(detection.scores.get("result_banner", 0.0), 3),
                        round(detection.scores.get("result_continue_text", 0.0), 3),
                        self._unknown_result_suspect_count,
                        round(now - self._unknown_since, 2) if self._unknown_since else 0.0,
                        round(now - self._last_battle_seen_at, 2) if self._last_battle_seen_at else -1.0,
                        len(unknown_board_state.hand_cards) if unknown_board_state is not None else -1,
                    )
                    self.mouse.click_region(self.regions["result_continue_button"], pause_seconds=1.0)
                    self._park_mouse()
                    self._mark_progress(now)
                    self._unknown_result_suspect_count = 0
                    time.sleep(self.config.poll_interval_seconds)
                    continue

                if self._handle_stagnant_progress(now, detection, board_state or unknown_board_state):
                    time.sleep(self.config.poll_interval_seconds)
                    continue

                action = decide_action(
                    scene=detection.scene,
                    board_state=board_state,
                    attempted_cards=self._attempted_cards_this_turn,
                )
                if (
                    detection.scene == "battle"
                    and board_state is not None
                    and board_state.can_end_turn
                    and board_state.hand_cards_ready
                    and self._battle_stall_count >= 6
                ):
                    logger.info("Battle stalled with playable candidates. Force end turn.")
                    action = decide_action(
                        scene="battle",
                        board_state=BoardState(
                            my_turn=True,
                            can_end_turn=True,
                            end_turn_active_score=board_state.end_turn_active_score,
                            mana_current=board_state.mana_current,
                            mana_total=board_state.mana_total,
                            hand_cards=[],
                        ),
                        attempted_cards=self._attempted_cards_this_turn,
                    )
                if detection.scene == "battle":
                    logger.info("Chosen action: {}", action.name)
                if (
                    action.name == "end_turn"
                    and (
                        now < self._end_turn_ready_at
                        or self._end_turn_confirm_count < self.config.end_turn_confirm_frames
                    )
                ):
                    logger.info(
                        "Delay end turn. cooldown_left={}s, confirm_frames={}/{}",
                        round(max(0.0, self._end_turn_ready_at - now), 2),
                        self._end_turn_confirm_count,
                        self.config.end_turn_confirm_frames,
                    )
                    action = decide_action(scene="matching")
                if action.name == "click_startup":
                    self._click_match(detection, "startup_entry", fallback_region="startup_entry", pause_seconds=1.0)
                    self._mark_progress(now)
                elif action.name == "click_main_battle":
                    self._queue_step = 0
                    self._pending_traditional_battle = True
                    self._click_match(
                        detection,
                        "main_battle_button",
                        fallback_region="main_battle_button",
                        pause_seconds=1.0,
                    )
                    self._mark_progress(now)
                elif action.name == "click_traditional_battle":
                    self._queue_step = 1
                    self._click_match(
                        detection,
                        "traditional_battle_button",
                        fallback_region="traditional_battle_button",
                        pause_seconds=1.0,
                    )
                    self._mark_progress(now)
                elif action.name == "prepare_match":
                    self._prepare_match(detection)
                    self._mark_progress(now)
                elif action.name == "confirm_match_error":
                    logger.info("Confirm queue match error dialog.")
                    self._click_match(
                        detection,
                        "confirm",
                        fallback_region="confirm_button",
                        pause_seconds=0.8,
                    )
                    self._queue_step = 1
                    self._last_queue_action_at = 0.0
                    self._mark_progress(now)
                elif action.name == "confirm_mulligan":
                    logger.info("Confirm mulligan.")
                    self._mulligan_grace_until = monotonic() + 60.0
                    self._click_match(
                        detection,
                        "mulligan_confirm",
                        fallback_region="mulligan_confirm_button",
                        pause_seconds=1.0,
                    )
                    self._mark_progress(now)
                elif action.name == "continue_result":
                    logger.info("Continue result screen.")
                    self.mouse.click_region(self.regions["result_continue_button"], pause_seconds=1.0)
                    self._park_mouse()
                    self._mark_progress(now)
                elif action.name == "confirm_result":
                    self._handle_result(detection)
                    self._mark_progress(now)
                elif action.name == "battle_wait":
                    if not self._battle_logged:
                        logger.info(
                            "Battle wait. reason={} decision_source={} ready={} mana={}/{}, final_cards={}",
                            action.params.get("reason"),
                            board_state.hand_source if board_state is not None else None,
                            board_state.hand_cards_ready if board_state is not None else None,
                            board_state.mana_current if board_state is not None else None,
                            board_state.mana_total if board_state is not None else None,
                            len(board_state.hand_cards) if board_state is not None else None,
                        )
                        self._battle_logged = True
                elif action.name == "play_card":
                    if self._turn_action_count < self.config.max_actions_per_turn:
                        drag_start = action.params["drag_start"]
                        self._last_play_attempt_card_id = str(action.params["card_id"])
                        logger.info(
                            "Play card {} from {} with ocr_conf={} cost={}",
                            self._last_play_attempt_card_id,
                            drag_start,
                            round(float(action.params["ocr_confidence"]), 3),
                            action.params.get("mana_cost"),
                        )
                        target = self._board_play_target()
                        logger.info("Drag target: {}", target)
                        self.mouse.drag(drag_start, target, duration=0.30)
                        self._park_mouse()
                        self._turn_action_count += 1
                        self._end_turn_ready_at = monotonic() + self.config.post_play_end_turn_delay_seconds
                        self._end_turn_confirm_count = 0
                        self._mark_progress(now)
                        time.sleep(0.8)
                elif action.name == "end_turn":
                    logger.info("End turn.")
                    if board_state is not None:
                        self._collect_ocr_turn_end_sample(frame, board_state)
                    self._click_match(
                        detection,
                        "end_turn",
                        fallback_region="end_turn",
                        pause_seconds=0.8,
                    )
                    self._turn_action_count = 0
                    self._attempted_cards_this_turn.clear()
                    self._last_play_attempt_card_id = None
                    self._battle_stall_count = 0
                    self._end_turn_ready_at = 0.0
                    self._end_turn_confirm_count = 0
                    self._mark_progress(now)
                time.sleep(self.config.poll_interval_seconds)
        finally:
            self.hotkeys.cleanup()

        logger.info("Bot stopped.")
        return 0

    def _park_mouse(self) -> None:
        self.mouse.move_to_safe_point(
            self.config.mouse_park_x,
            self.config.mouse_park_y,
        )

    def _click_match(
        self,
        detection: SceneDetection,
        match_name: str,
        fallback_region: str,
        pause_seconds: float,
    ) -> None:
        match = detection.matches.get(match_name)
        if match is not None:
            center_x = match.x + (match.width // 2)
            center_y = match.y + (match.height // 2)
            if match_name == "traditional_battle_button":
                center_y += max(18, match.height // 3)
            logger.info(
                "Click match {} at ({}, {}) size={}x{} score={}",
                match_name,
                center_x,
                center_y,
                match.width,
                match.height,
                round(match.score, 3),
            )
            self.mouse.click_point(center_x, center_y, pause_seconds=pause_seconds)
            self._park_mouse()
            return
        fallback = self.regions[fallback_region]
        logger.info(
            "Click fallback region {} at ({}, {}) size={}x{}",
            fallback_region,
            fallback.x + (fallback.w // 2),
            fallback.y + (fallback.h // 2),
            fallback.w,
            fallback.h,
        )
        self.mouse.click_region(fallback, pause_seconds=pause_seconds)
        self._park_mouse()

    def _prepare_match(self, detection: SceneDetection) -> None:
        now = monotonic()
        if now - self._last_queue_action_at < 1.5:
            return
        deck_point = self.deck_slots.get(self.config.deck_index)
        if deck_point is None:
            raise RuntimeError(f"Deck slot {self.config.deck_index} is not configured.")
        if self._queue_step == 0:
            logger.info("Queue flow: click traditional battle entry.")
            self._click_match(
                detection,
                "traditional_battle_button",
                fallback_region="traditional_battle_button",
                pause_seconds=1.0,
            )
            self._pending_traditional_battle = False
            self._queue_step = 1
            self._last_queue_action_at = monotonic()
            return
        logger.info("Queue flow: select deck {} and click play.", self.config.deck_index)
        logger.info("Deck slot click point: {}", deck_point)
        self.mouse.click_point(*deck_point, pause_seconds=0.6)
        self._park_mouse()
        self._click_match(
            detection,
            "queue_play_button",
            fallback_region="queue_play_button",
            pause_seconds=1.0,
        )
        self._last_queue_action_at = monotonic()

    def _handle_result(self, detection: SceneDetection) -> None:
        logger.info("Handling result screen.")
        if self._result_click_count < self.config.max_result_clicks:
            self._click_match(
                detection,
                "confirm",
                fallback_region="confirm_button",
                pause_seconds=0.8,
            )
            self._result_click_count += 1
        self._pending_traditional_battle = False
        self._queue_step = 1

    def _build_battle_sample_metadata(
        self,
        *,
        sample_id: str,
        trigger_reason: str,
        all_trigger_reasons: tuple[str, ...],
        scene: str,
        board_state: BoardState,
        hand_debug_entries: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        hand_debug_entries = hand_debug_entries or []
        return {
            "sample_id": sample_id,
            "resolution": f"{self.config.window_width}x{self.config.window_height}",
            "scene": scene,
            "sample_kind": "ocr_anomaly",
            "hand_source": board_state.hand_source,
            "hand_cards_ready": board_state.hand_cards_ready,
            "ocr_trusted": board_state.ocr_trusted,
            "ocr_reject_reasons": list(board_state.ocr_reject_reasons),
            "mana_current": board_state.mana_current,
            "mana_total": board_state.mana_total,
            "final_cards_count": len(board_state.hand_cards),
            "debug_candidate_count": len(hand_debug_entries),
            "trigger_reason": trigger_reason,
            "all_trigger_reasons": list(all_trigger_reasons),
        }

    def _detect_battle_anomaly_triggers(
        self,
        board_state: BoardState,
        hand_debug_entries: list[dict[str, object]] | None = None,
    ) -> tuple[str, ...]:
        hand_debug_entries = hand_debug_entries or []
        reject_reasons = tuple(board_state.ocr_reject_reasons)
        reason_text = "|".join(reject_reasons)
        triggers: list[str] = []

        if board_state.hand_source == "ocr_wait_mana":
            if any("jump:" in reason or "_out_of_range:" in reason or "_gt_total:" in reason for reason in reject_reasons):
                triggers.append("mana_validation_failed")
            if reject_reasons:
                triggers.append("ocr_wait_mana")
        if board_state.hand_source == "ocr_wait_cost":
            triggers.append("ocr_wait_cost")
        if not board_state.hand_cards_ready:
            triggers.append(f"hand_cards_not_ready:{board_state.hand_source}")
        if not board_state.ocr_trusted:
            triggers.append("ocr_untrusted")
        if len(hand_debug_entries) > 0 and len(board_state.hand_cards) == 0:
            triggers.append("legacy_candidates_without_final_cards")
        if "cost_reject:" in reason_text:
            triggers.append("cost_ocr_untrusted")

        deduped: list[str] = []
        for trigger in triggers:
            if trigger not in deduped:
                deduped.append(trigger)
        return tuple(deduped)

    def _collect_battle_anomaly_sample(
        self,
        *,
        frame: np.ndarray,
        scene: str,
        board_state: BoardState,
        hand_debug_entries: list[dict[str, object]] | None,
        now: float,
    ) -> None:
        if not self.config.ocr_anomaly_sample_enabled:
            return
        all_trigger_reasons = self._detect_battle_anomaly_triggers(board_state, hand_debug_entries)
        if not all_trigger_reasons:
            return
        trigger_reason = all_trigger_reasons[0]

        debug_candidate_count = len(hand_debug_entries or [])
        signature = (
            trigger_reason,
            board_state.hand_source,
            len(board_state.hand_cards),
            debug_candidate_count,
        )
        if signature == self._last_anomaly_sample_signature:
            if (now - self._last_anomaly_sample_at) < self.config.ocr_anomaly_sample_cooldown_seconds:
                return

        sample_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            window = self.capture.find_window()
            extra_crops = self._build_hand_cost_sample_crops(frame, board_state)
            metadata = self._build_battle_sample_metadata(
                sample_id=sample_id,
                trigger_reason=trigger_reason,
                all_trigger_reasons=all_trigger_reasons,
                scene=scene,
                board_state=board_state,
                hand_debug_entries=hand_debug_entries,
            )
            self.sampler.collect_from_frame(
                tag=self.config.ocr_anomaly_sample_tag,
                frame=frame,
                window=window,
                include_regions=True,
                region_names=["mana", "hand"],
                metadata=metadata,
                extra_crops=extra_crops,
                sample_id=sample_id,
            )
            self._last_anomaly_sample_signature = signature
            self._last_anomaly_sample_at = now
            logger.warning(
                "Captured OCR anomaly sample. sample_id={} trigger_reason={} all_trigger_reasons={} source={} ready={} trusted={} final_cards={} debug_candidates={} mana={}/{}",
                sample_id,
                trigger_reason,
                list(all_trigger_reasons),
                board_state.hand_source,
                board_state.hand_cards_ready,
                board_state.ocr_trusted,
                len(board_state.hand_cards),
                debug_candidate_count,
                board_state.mana_current,
                board_state.mana_total,
            )
        except Exception as exc:
            logger.warning("OCR anomaly sample failed. trigger_reason={} error={}", trigger_reason, exc)

    def _collect_ocr_turn_end_sample(
        self,
        frame: np.ndarray,
        board_state: BoardState,
    ) -> None:
        if not self.config.ocr_auto_sample_enabled:
            return
        signature = (
            len(board_state.hand_cards),
            tuple(card.card_id for card in board_state.hand_cards),
        )
        if signature == self._last_ocr_sample_signature:
            return
        try:
            window = self.capture.find_window()
            extra_crops = self._build_hand_cost_sample_crops(frame, board_state)
            self.sampler.collect_from_frame(
                tag=self.config.ocr_auto_sample_tag,
                frame=frame,
                window=window,
                include_regions=True,
                region_names=["mana", "hand"],
                metadata={
                    "scene": "battle_end_turn",
                    "sample_kind": "ocr_auto_turn_end",
                    "can_end_turn": board_state.can_end_turn,
                    "end_turn_active_score": round(board_state.end_turn_active_score, 3),
                    "detected_cards": len(board_state.hand_cards),
                    "card_ids": ",".join(card.card_id for card in board_state.hand_cards),
                    "cost_crop_count": len(extra_crops),
                },
                extra_crops=extra_crops,
            )
            self._last_ocr_sample_signature = signature
        except Exception as exc:
            logger.warning("OCR auto sample failed: {}", exc)

    def _validate_mana_values(
        self,
        mana_current: int,
        mana_total: int,
        can_end_turn: bool,
    ) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if mana_current < 0 or mana_current > 10:
            reasons.append(f"mana_current_out_of_range:{mana_current}")
        if mana_total < 0 or mana_total > 10:
            reasons.append(f"mana_total_out_of_range:{mana_total}")
        if mana_current > mana_total:
            reasons.append(f"mana_current_gt_total:{mana_current}>{mana_total}")
        if self._last_trusted_mana_state is not None:
            prev_current, prev_total, prev_can_end_turn = self._last_trusted_mana_state
            if prev_can_end_turn == can_end_turn:
                if abs(mana_total - prev_total) > 1:
                    reasons.append(f"mana_total_jump:{prev_total}->{mana_total}")
                if abs(mana_current - prev_current) > 4:
                    reasons.append(f"mana_current_jump:{prev_current}->{mana_current}")
        return len(reasons) == 0, tuple(reasons)

    def _recognize_mana_text(
        self,
        frame: np.ndarray,
        can_end_turn: bool,
    ) -> tuple[int | None, int | None, float, tuple[str, ...]]:
        mana_crop = crop_region(frame, self.regions["mana"])
        decision = self.dataset_ocr.recognize_mana(mana_crop)
        reasons = list(decision.reasons)
        label = decision.label
        if not label:
            return None, None, decision.confidence, tuple(reasons or ["mana_ocr_rejected"])
        if "/" not in label:
            return None, None, decision.confidence, tuple(reasons + [f"mana_format_invalid:{label}"])
        current_text, total_text = label.split("/", 1)
        if not current_text.isdigit() or not total_text.isdigit():
            return None, None, decision.confidence, tuple(reasons + [f"mana_non_digit:{label}"])
        mana_current = int(current_text)
        mana_total = int(total_text)
        trusted, validation_reasons = self._validate_mana_values(mana_current, mana_total, can_end_turn)
        if not trusted:
            return None, None, decision.confidence, tuple(reasons + list(validation_reasons))
        self._last_trusted_mana_state = (mana_current, mana_total, can_end_turn)
        return mana_current, mana_total, decision.confidence, tuple(reasons)

    def _build_ocr_hand_cards(
        self,
        frame: np.ndarray,
        mana_current: int | None,
    ) -> tuple[list[HandCard], tuple[str, ...]]:
        hand_region = self.regions["hand"]
        hand_frame = frame[
            hand_region.y : hand_region.y + hand_region.h,
            hand_region.x : hand_region.x + hand_region.w,
        ]
        if hand_frame.size == 0:
            return [], ("hand_frame_empty",)

        cards: list[HandCard] = []
        reject_reasons: list[str] = []
        validated_candidates = self._detect_hand_cost_gems(hand_frame)
        for candidate in validated_candidates:
            crop = self._extract_cost_crop(frame, hand_region, candidate)
            if crop is None or crop.size == 0:
                reject_reasons.append(f"cost_crop_invalid:{candidate.center_x},{candidate.center_y}")
                continue

            decision = self.dataset_ocr.recognize_cost(crop)
            label = decision.label
            if label is None or not label.isdigit():
                reason_text = ",".join(decision.reasons) if decision.reasons else "cost_ocr_rejected"
                logger.debug(
                    "OCR cost reject: center=({}, {}) source={} score={} conf={} best_diff={} second_diff={} reasons={}"
                    ,
                    candidate.center_x,
                    candidate.center_y,
                    candidate.source,
                    round(candidate.metrics.score, 3),
                    round(decision.confidence, 3),
                    round(decision.best_diff, 4),
                    round(decision.second_diff, 4),
                    reason_text,
                )
                reject_reasons.append(f"cost_reject:{candidate.center_x},{candidate.center_y}:{reason_text}")
                continue


            mana_cost = int(label)
            if mana_cost < 0 or mana_cost > 20:
                reason_text = f"cost_out_of_range:{mana_cost}"
                logger.debug(
                    "OCR cost reject: center=({}, {}) source={} score={} conf={} label={} reasons={}"
                    ,
                    candidate.center_x,
                    candidate.center_y,
                    candidate.source,
                    round(candidate.metrics.score, 3),
                    round(decision.confidence, 3),
                    label,
                    reason_text,
                )
                reject_reasons.append(f"cost_reject:{candidate.center_x},{candidate.center_y}:{reason_text}")
                continue

            global_x = hand_region.x + candidate.center_x
            global_y = hand_region.y + candidate.center_y
            bbox_x, bbox_y, bbox_w, bbox_h = candidate.bbox
            cards.append(
                HandCard(
                    card_id=f"{global_x}:{global_y}",
                    anchor_center=(global_x, global_y),
                    drag_start=(global_x, min(self.config.window_height - 12, hand_region.y + hand_region.h - 8)),
                    bbox=(hand_region.x + bbox_x, hand_region.y + bbox_y, bbox_w, bbox_h),
                    playable_score=decision.confidence,
                    playable=(mana_current is not None and mana_cost <= mana_current),
                    mana_cost=mana_cost,
                    ocr_confidence=decision.confidence,
                )
            )
        cards.sort(key=lambda card: card.anchor_center[0])
        return cards, tuple(reject_reasons)

    def _apply_ocr_board_state(
        self,
        frame: np.ndarray,
        board_state: BoardState,
        hand_debug_entries: list[dict[str, object]] | None = None,
    ) -> BoardState:
        """Attach OCR-derived mana + hand cards onto battle base state.

        parse_board_state() only provides battle base status. Final battle
        hand_cards are produced here from the OCR/cost-gem path only. Legacy
        green-highlight detections may be logged as debug hints, but are never
        promoted into the returned hand_cards.
        """
        mana_current, mana_total, mana_confidence, mana_reasons = self._recognize_mana_text(
            frame,
            can_end_turn=board_state.can_end_turn,
        )
        ocr_hand_cards, cost_reasons = self._build_ocr_hand_cards(frame, mana_current)
        hand_debug_entries = hand_debug_entries or []
        debug_candidate_count = len(hand_debug_entries)

        hand_cards_ready = False
        hand_source = "ocr_pending"
        ocr_trusted = False
        reject_reasons = list(mana_reasons)
        final_mana_current = mana_current if mana_current is not None else board_state.mana_current
        final_mana_total = mana_total if mana_total is not None else board_state.mana_total

        if mana_current is None or mana_total is None:
            hand_source = "ocr_wait_mana"
        elif ocr_hand_cards:
            hand_cards_ready = True
            hand_source = "ocr_cards"
            ocr_trusted = True
            reject_reasons.extend(cost_reasons)
        elif debug_candidate_count > 0:
            hand_source = "ocr_wait_cost"
            reject_reasons.extend(cost_reasons)
        else:
            hand_cards_ready = True
            hand_source = "ocr_empty_trusted"
            ocr_trusted = True
            reject_reasons.extend(cost_reasons)

        logger.debug(
            "OCR decision input: mana={}/{} conf={} trusted={} source={} ready={} mana_reasons={} cost_reasons={} debug_candidate_count={} final_cards={}",
            final_mana_current,
            final_mana_total,
            round(mana_confidence, 3),
            ocr_trusted,
            hand_source,
            hand_cards_ready,
            list(mana_reasons),
            list(cost_reasons),
            debug_candidate_count,
            [
                {
                    "id": card.card_id,
                    "cost": card.mana_cost,
                    "playable": card.playable,
                    "conf": round(card.ocr_confidence, 3),
                }
                for card in ocr_hand_cards
            ],
        )
        return BoardState(
            my_turn=board_state.my_turn,
            can_end_turn=board_state.can_end_turn,
            end_turn_active_score=board_state.end_turn_active_score,
            mana_current=int(final_mana_current),
            mana_total=int(final_mana_total),
            hand_cards=ocr_hand_cards if hand_cards_ready else [],
            hand_source=hand_source,
            hand_cards_ready=hand_cards_ready,
            ocr_trusted=ocr_trusted,
            ocr_reject_reasons=tuple(reject_reasons),
        )

    def _build_hand_cost_sample_crops(
        self,
        frame: np.ndarray,
        board_state: BoardState,
    ) -> dict[str, np.ndarray]:
        del board_state
        crops: dict[str, np.ndarray] = {}
        hand_region = self.regions["hand"]
        hand_frame = frame[
            hand_region.y : hand_region.y + hand_region.h,
            hand_region.x : hand_region.x + hand_region.w,
        ]
        if hand_frame.size == 0:
            return crops

        gem_targets = self._detect_hand_cost_gems(hand_frame)
        for index, candidate in enumerate(gem_targets, start=1):
            crop = self._extract_cost_crop(frame, hand_region, candidate)
            if crop is None or crop.size == 0:
                continue
            safe_card_id = f"{hand_region.x + candidate.center_x}_{hand_region.y + candidate.center_y}"
            crops[f"cost_card_{index:02d}_{safe_card_id}"] = crop
        return crops

    def _detect_hand_cost_gems(self, hand_frame: np.ndarray) -> list[GemCandidate]:
        candidates = self._generate_hand_cost_gem_candidates(hand_frame)
        if not candidates:
            return []
        validated = self._validate_hand_cost_gem_candidates(hand_frame, candidates)
        logger.debug(
            "Gem detect: generated={} validated={} xs={} scores={}",
            len(candidates),
            len(validated),
            [candidate.center_x for candidate in validated],
            [round(candidate.metrics.score, 3) for candidate in validated],
        )
        return validated

    def _generate_hand_cost_gem_candidates(self, hand_frame: np.ndarray) -> list[tuple[int, int, int, str]]:
        hsv = cv2.cvtColor(hand_frame, cv2.COLOR_BGR2HSV)
        blue_mask = cv2.inRange(hsv, (88, 45, 45), (132, 255, 255))
        blue_mask = cv2.GaussianBlur(blue_mask, (7, 7), 1.5)

        raw_candidates: list[tuple[int, int, int, str]] = []
        circles = cv2.HoughCircles(
            blue_mask,
            cv2.HOUGH_GRADIENT,
            dp=1.15,
            minDist=16,
            param1=55,
            param2=8,
            minRadius=7,
            maxRadius=19,
        )
        if circles is not None:
            for circle in np.round(circles[0]).astype(int):
                x, y, radius = (int(circle[0]), int(circle[1]), int(circle[2]))
                if self._is_reasonable_gem_position(hand_frame, x, y, radius):
                    raw_candidates.append((x, y, radius, "hough"))

        mask_binary = cv2.inRange(hsv, (88, 55, 55), (132, 255, 255))
        contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 80 or area > 1400:
                continue
            (x, y), radius = cv2.minEnclosingCircle(contour)
            x_i, y_i, radius_i = int(round(x)), int(round(y)), int(round(radius))
            if not self._is_reasonable_gem_position(hand_frame, x_i, y_i, radius_i):
                continue
            raw_candidates.append((x_i, y_i, radius_i, "contour"))

        raw_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        deduped: list[tuple[int, int, int, str]] = []
        for x, y, radius, source in raw_candidates:
            if not deduped:
                deduped.append((x, y, radius, source))
                continue
            prev_x, prev_y, prev_radius, prev_source = deduped[-1]
            center_distance = ((x - prev_x) ** 2 + (y - prev_y) ** 2) ** 0.5
            if center_distance <= max(radius, prev_radius, 10):
                if y < prev_y or (y == prev_y and source == "hough" and prev_source != "hough"):
                    deduped[-1] = (x, y, radius, source)
                continue
            deduped.append((x, y, radius, source))
        return deduped

    def _validate_hand_cost_gem_candidates(
        self,
        hand_frame: np.ndarray,
        raw_candidates: list[tuple[int, int, int, str]],
    ) -> list[GemCandidate]:
        evaluated: list[GemCandidate] = []
        for x, y, radius, source in raw_candidates:
            metrics = self._measure_gem_candidate(hand_frame, x, y, radius)
            if metrics is None:
                continue
            if metrics.score < 0.43:
                continue
            bbox = self._build_hand_local_cost_bbox(hand_frame, x, y, radius)
            if bbox is None:
                continue
            evaluated.append(
                GemCandidate(
                    center_x=x,
                    center_y=y,
                    radius=radius,
                    bbox=bbox,
                    metrics=metrics,
                    source=source,
                )
            )

        if not evaluated:
            return []

        evaluated.sort(key=lambda item: item.center_x)
        spacing_values = [
            evaluated[index + 1].center_x - evaluated[index].center_x
            for index in range(len(evaluated) - 1)
            if 14 <= (evaluated[index + 1].center_x - evaluated[index].center_x) <= 120
        ]
        median_spacing = float(np.median(spacing_values)) if spacing_values else 0.0

        filtered: list[GemCandidate] = []
        for index, candidate in enumerate(evaluated):
            nearest_gap = min(
                [
                    abs(candidate.center_x - other.center_x)
                    for other_index, other in enumerate(evaluated)
                    if other_index != index
                ],
                default=0,
            )
            if nearest_gap and nearest_gap < max(12, int(candidate.radius * 1.1)):
                if filtered and abs(filtered[-1].center_x - candidate.center_x) < max(12, int(candidate.radius * 1.1)):
                    if candidate.metrics.score > filtered[-1].metrics.score:
                        filtered[-1] = candidate
                    continue
            if median_spacing > 0 and nearest_gap > median_spacing * 2.4 and candidate.metrics.score < 0.58:
                logger.debug(
                    "Gem reject by spacing: center=({}, {}) gap={} median_gap={} score={}",
                    candidate.center_x,
                    candidate.center_y,
                    nearest_gap,
                    round(median_spacing, 1),
                    round(candidate.metrics.score, 3),
                )
                continue
            filtered.append(candidate)

        return filtered

    def _is_reasonable_gem_position(self, hand_frame: np.ndarray, center_x: int, center_y: int, radius: int) -> bool:
        height, width = hand_frame.shape[:2]
        if radius < 7 or radius > 19:
            return False
        if center_y >= min(58, height - 6):
            return False
        if center_y <= 4 or center_x <= 4 or center_x >= width - 4:
            return False
        return True

    def _build_hand_local_cost_bbox(
        self,
        hand_frame: np.ndarray,
        center_x: int,
        center_y: int,
        radius: int,
    ) -> tuple[int, int, int, int] | None:
        height, width = hand_frame.shape[:2]
        x1 = max(0, int(center_x - (radius * 1.55)))
        y1 = max(0, int(center_y - (radius * 1.35)))
        x2 = min(width, int(center_x + (radius * 1.55)))
        y2 = min(height, int(center_y + (radius * 1.35)))
        if x2 <= x1 or y2 <= y1:
            return None
        if (x2 - x1) < 28 or (y2 - y1) < 28:
            return None
        if (x2 - x1) > 44 or (y2 - y1) > 38:
            return None
        return x1, y1, x2 - x1, y2 - y1

    def _extract_cost_crop(
        self,
        frame: np.ndarray,
        hand_region,
        candidate: GemCandidate,
    ) -> np.ndarray | None:
        x, y, w, h = candidate.bbox
        x1 = hand_region.x + x
        y1 = hand_region.y + y
        x2 = x1 + w
        y2 = y1 + h
        crop = frame[y1:y2, x1:x2].copy()
        if crop.size == 0 or not self._is_valid_cost_crop(crop):
            return None
        return crop

    def _measure_gem_candidate(
        self,
        hand_frame: np.ndarray,
        center_x: int,
        center_y: int,
        radius: int,
    ) -> GemValidationMetrics | None:
        bbox = self._build_hand_local_cost_bbox(hand_frame, center_x, center_y, radius)
        if bbox is None:
            return None
        x, y, w, h = bbox
        roi = hand_frame[y : y + h, x : x + w]
        if roi.size == 0:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        blue_mask = (hue >= 88) & (hue <= 132) & (saturation >= 60) & (value >= 55)
        white_mask = (saturation <= 80) & (value >= 150)

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 140) > 0
        yy, xx = np.ogrid[:h, :w]
        ellipse_mask = (((xx - (w / 2.0)) / max(1.0, w * 0.42)) ** 2 + ((yy - (h / 2.0)) / max(1.0, h * 0.42)) ** 2) <= 1.0
        ring_mask = (((xx - (w / 2.0)) / max(1.0, w * 0.48)) ** 2 + ((yy - (h / 2.0)) / max(1.0, h * 0.48)) ** 2) <= 1.0
        ring_mask &= ~((((xx - (w / 2.0)) / max(1.0, w * 0.30)) ** 2 + ((yy - (h / 2.0)) / max(1.0, h * 0.30)) ** 2) <= 1.0)

        blue_ratio = float(blue_mask[ellipse_mask].mean()) if np.any(ellipse_mask) else 0.0
        white_ratio = float(white_mask[ellipse_mask].mean()) if np.any(ellipse_mask) else 0.0
        fill_ratio = float(blue_mask.mean())
        edge_density = float(edges[ring_mask].mean()) if np.any(ring_mask) else 0.0

        blue_u8 = (blue_mask.astype(np.uint8) * 255)
        contours, _ = cv2.findContours(blue_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        circularity = 0.0
        if contours:
            contour = max(contours, key=cv2.contourArea)
            area = float(cv2.contourArea(contour))
            perimeter = float(cv2.arcLength(contour, True))
            if perimeter > 1e-3:
                circularity = float((4.0 * np.pi * area) / (perimeter * perimeter))

        score = (
            min(1.0, blue_ratio / 0.22) * 0.34
            + min(1.0, white_ratio / 0.11) * 0.22
            + min(1.0, circularity / 0.62) * 0.22
            + min(1.0, edge_density / 0.18) * 0.12
            + min(1.0, fill_ratio / 0.18) * 0.10
        )
        return GemValidationMetrics(
            blue_ratio=blue_ratio,
            white_ratio=white_ratio,
            circularity=circularity,
            fill_ratio=fill_ratio,
            edge_density=edge_density,
            score=float(score),
        )

    def _is_valid_cost_crop(self, crop: np.ndarray) -> bool:
        height, width = crop.shape[:2]
        if width < 28 or height < 28:
            return False
        if width > 44 or height > 38:
            return False

        metrics = self._measure_gem_candidate(
            crop,
            center_x=width // 2,
            center_y=height // 2,
            radius=max(8, min(width, height) // 3),
        )
        if metrics is None:
            return False
        contrast = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std()
        return bool(
            metrics.blue_ratio >= 0.07
            and metrics.white_ratio >= 0.04
            and metrics.circularity >= 0.22
            and metrics.score >= 0.42
            and contrast >= 40.0
        )
