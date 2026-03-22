from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bot.regions import Region
from bot.template_index import TemplateSpec
from bot.vision.matcher import MatchResult, match_template


@dataclass(frozen=True)
class SceneDetection:
    scene: str
    scores: dict[str, float]
    matches: dict[str, MatchResult]


def _score(
    frame: np.ndarray,
    regions: dict[str, Region],
    specs: dict[str, TemplateSpec],
    region_name: str,
    template_name: str,
) -> MatchResult:
    return match_template(frame, regions[region_name], specs[template_name])


def detect_scene(
    frame: np.ndarray,
    regions: dict[str, Region],
    specs: dict[str, TemplateSpec],
) -> SceneDetection:
    matches: dict[str, MatchResult] = {
        "back_button": _score(frame, regions, specs, "back_button", "back_button"),
        "startup_entry": _score(frame, regions, specs, "startup_entry", "startup_entry"),
        "main_battle_button": _score(frame, regions, specs, "main_battle_button", "main_battle_button"),
        "traditional_battle_button": _score(
            frame,
            regions,
            specs,
            "traditional_battle_button",
            "traditional_battle_button",
        ),
        "casual_mode_button": _score(frame, regions, specs, "casual_mode_button", "casual_mode_button"),
        "queue_play_button": _score(frame, regions, specs, "queue_play_button", "queue_play_button"),
        "end_turn": _score(frame, regions, specs, "end_turn", "end_turn"),
        "result_banner": _score(frame, regions, specs, "result_banner", "result_banner"),
        "result_continue_text": _score(
            frame,
            regions,
            specs,
            "result_continue_text",
            "result_continue_text",
        ),
        "confirm": _score(frame, regions, specs, "confirm_button", "confirm"),
        "mulligan_confirm": _score(
            frame,
            regions,
            specs,
            "mulligan_confirm_button",
            "mulligan_confirm",
        ),
    }
    scores = {name: result.score for name, result in matches.items()}

    if scores["mulligan_confirm"] >= specs["mulligan_confirm"].threshold:
        scene = "mulligan"
    elif (
        scores["result_banner"] >= specs["result_banner"].threshold
        or scores["result_continue_text"] >= specs["result_continue_text"].threshold
    ):
        scene = "result_continue"
    elif scores["confirm"] >= specs["confirm"].threshold:
        scene = "result"
    elif scores["end_turn"] >= specs["end_turn"].threshold:
        scene = "battle"
    elif (
        scores["back_button"] >= specs["back_button"].threshold
        and scores["queue_play_button"] >= specs["queue_play_button"].threshold
    ):
        scene = "queue_page"
    elif scores["traditional_battle_button"] >= specs["traditional_battle_button"].threshold:
        scene = "battle_menu"
    elif (
        scores["back_button"] >= specs["back_button"].threshold
        and 0.40 <= scores["queue_play_button"] < specs["queue_play_button"].threshold
    ):
        scene = "matching"
    elif scores["casual_mode_button"] >= specs["casual_mode_button"].threshold:
        scene = "battle_menu"
    elif scores["main_battle_button"] >= specs["main_battle_button"].threshold:
        scene = "main_menu"
    elif scores["startup_entry"] >= specs["startup_entry"].threshold:
        scene = "startup"
    else:
        scene = "unknown"

    return SceneDetection(scene=scene, scores=scores, matches=matches)
