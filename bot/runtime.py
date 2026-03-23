from __future__ import annotations

import time
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
        self._last_battle_signature: tuple[int, tuple[int, ...]] | None = None
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
                    board_state = self._apply_ocr_board_state(frame, board_state)
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
                    elif any(card.playable for card in board_state.hand_cards):
                        self._battle_stall_count += 1
                    logger.info(
                        "Battle state: detected_cards={}, mana={}/{}, end_turn_active_score={}, stall_count={}, playable_scores={}, attempted={}",
                        len(board_state.hand_cards),
                        board_state.mana_current,
                        board_state.mana_total,
                        round(board_state.end_turn_active_score, 3),
                        self._battle_stall_count,
                        [
                            {
                                "id": card.card_id,
                                "cost": card.mana_cost,
                                "score": round(card.playable_score, 3),
                                "playable": card.playable,
                            }
                            for card in board_state.hand_cards
                        ],
                        sorted(self._attempted_cards_this_turn),
                    )
                    logger.debug(
                        "Battle hand detection: {}",
                        build_hand_debug_entries(
                            frame,
                            self.regions["hand"],
                            self.hand_detection_config,
                        ),
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
                        logger.info("Battle scene reached. Battle automation is not implemented yet.")
                        self._battle_logged = True
                elif action.name == "play_card":
                    if self._turn_action_count < self.config.max_actions_per_turn:
                        drag_start = action.params["drag_start"]
                        self._last_play_attempt_card_id = str(action.params["card_id"])
                        logger.info(
                            "Play card {} from {} with score={} cost={}",
                            self._last_play_attempt_card_id,
                            drag_start,
                            round(float(action.params["playable_score"]), 3),
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

    def _recognize_mana_text(self, frame: np.ndarray) -> tuple[int | None, int | None, float]:
        mana_crop = crop_region(frame, self.regions["mana"])
        label, confidence = self.dataset_ocr.recognize_mana(mana_crop)
        if not label or "/" not in label:
            return None, None, confidence
        current_text, total_text = label.split("/", 1)
        if not current_text.isdigit() or not total_text.isdigit():
            return None, None, confidence
        return int(current_text), int(total_text), confidence

    def _build_ocr_hand_cards(self, frame: np.ndarray, mana_current: int | None) -> list[HandCard]:
        hand_region = self.regions["hand"]
        hand_frame = frame[
            hand_region.y : hand_region.y + hand_region.h,
            hand_region.x : hand_region.x + hand_region.w,
        ]
        if hand_frame.size == 0:
            return []

        cards: list[HandCard] = []
        frame_height, frame_width = frame.shape[:2]
        for center_x, center_y, radius in self._detect_hand_cost_gems(hand_frame):
            global_x = hand_region.x + center_x
            global_y = hand_region.y + center_y
            x1 = max(0, int(global_x - (radius * 1.55)))
            y1 = max(0, int(global_y - (radius * 1.35)))
            x2 = min(frame_width, int(global_x + (radius * 1.55)))
            y2 = min(frame_height, int(global_y + (radius * 1.35)))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2].copy()
            if crop.size == 0 or not self._is_valid_cost_crop(crop):
                continue

            label, confidence = self.dataset_ocr.recognize_cost(crop)
            if label is None or not label.isdigit():
                continue
            mana_cost = int(label)
            cards.append(
                HandCard(
                    card_id=f"{global_x}:{global_y}",
                    anchor_center=(global_x, global_y),
                    drag_start=(global_x, min(self.config.window_height - 12, hand_region.y + hand_region.h - 8)),
                    bbox=(x1, y1, x2 - x1, y2 - y1),
                    playable_score=confidence,
                    playable=(mana_current is not None and mana_cost <= mana_current),
                    mana_cost=mana_cost,
                    ocr_confidence=confidence,
                )
            )
        cards.sort(key=lambda card: card.anchor_center[0])
        return cards

    def _apply_ocr_board_state(self, frame: np.ndarray, board_state: BoardState) -> BoardState:
        mana_current, mana_total, mana_confidence = self._recognize_mana_text(frame)
        ocr_hand_cards = self._build_ocr_hand_cards(frame, mana_current)

        if mana_current is None or mana_total is None:
            mana_current = board_state.mana_current
            mana_total = board_state.mana_total
            ocr_hand_cards = board_state.hand_cards
        elif not ocr_hand_cards:
            ocr_hand_cards = board_state.hand_cards

        logger.debug(
            "OCR state: mana={}/{} conf={} cards={}",
            mana_current,
            mana_total,
            round(mana_confidence, 3),
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
            mana_current=int(mana_current),
            mana_total=int(mana_total),
            hand_cards=ocr_hand_cards,
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
        frame_height, frame_width = frame.shape[:2]
        for index, (center_x, center_y, radius) in enumerate(gem_targets, start=1):
            global_x = hand_region.x + center_x
            global_y = hand_region.y + center_y
            x1 = max(0, int(global_x - (radius * 1.55)))
            y1 = max(0, int(global_y - (radius * 1.35)))
            x2 = min(frame_width, int(global_x + (radius * 1.55)))
            y2 = min(frame_height, int(global_y + (radius * 1.35)))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2].copy()
            if crop.size == 0:
                continue
            if not self._is_valid_cost_crop(crop):
                continue
            safe_card_id = f"{global_x}_{global_y}"
            crops[f"cost_card_{index:02d}_{safe_card_id}"] = crop
        return crops

    def _detect_hand_cost_gems(self, hand_frame: np.ndarray) -> list[tuple[int, int, int]]:
        hsv = cv2.cvtColor(hand_frame, cv2.COLOR_BGR2HSV)
        blue_mask = cv2.inRange(
            hsv,
            (90, 60, 60),
            (130, 255, 255),
        )
        blurred_mask = cv2.GaussianBlur(blue_mask, (9, 9), 2)
        circles = cv2.HoughCircles(
            blurred_mask,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=22,
            param1=60,
            param2=12,
            minRadius=8,
            maxRadius=18,
        )
        if circles is None:
            return []

        raw_circles = [
            tuple(int(value) for value in circle)
            for circle in np.round(circles[0]).astype(int)
            if int(circle[1]) < min(55, hand_frame.shape[0] - 6)
        ]
        raw_circles.sort(key=lambda item: (item[0], item[1]))

        deduped: list[tuple[int, int, int]] = []
        for x, y, radius in raw_circles:
            if deduped and abs(x - deduped[-1][0]) < 34:
                if y < deduped[-1][1]:
                    deduped[-1] = (x, y, radius)
                continue
            deduped.append((x, y, radius))
        return deduped

    def _is_valid_cost_crop(self, crop: np.ndarray) -> bool:
        height, width = crop.shape[:2]
        if width < 28 or height < 28:
            return False
        if width > 43 or height > 37:
            return False

        roi = crop[
            int(height * 0.10) : int(height * 0.70),
            int(width * 0.10) : int(width * 0.70),
        ]
        if roi.size == 0:
            return False

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        blue_ratio = (
            (hue >= 90)
            & (hue <= 130)
            & (saturation >= 70)
            & (value >= 70)
        ).mean()
        white_ratio = (
            (saturation <= 70)
            & (value >= 150)
        ).mean()
        contrast = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).std()
        return bool(
            blue_ratio >= 0.08
            and white_ratio >= 0.05
            and contrast >= 45.0
        )
