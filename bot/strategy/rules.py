from __future__ import annotations

from dataclasses import dataclass, field

from bot.vision.board_state import BoardState


@dataclass(frozen=True)
class Action:
    name: str
    params: dict[str, object] = field(default_factory=dict)


def decide_action(
    scene: str,
    board_state: BoardState | None = None,
    attempted_cards: set[str] | None = None,
) -> Action:
    if scene == "startup":
        return Action("click_startup")
    if scene == "main_menu":
        return Action("click_main_battle")
    if scene == "battle_menu":
        return Action("click_traditional_battle")
    if scene == "mulligan":
        return Action("confirm_mulligan")
    if scene == "queue_page":
        return Action("prepare_match")
    if scene == "result_continue":
        return Action("continue_result")
    if scene == "result":
        return Action("confirm_result")
    if scene == "battle":
        if board_state is None or not board_state.my_turn:
            return Action("wait")
        attempted_cards = attempted_cards or set()
        available_cards = [
            card
            for card in board_state.hand_cards
            if card.card_id not in attempted_cards and card.playable
        ]
        if available_cards:
            chosen = max(available_cards, key=lambda card: (card.playable_score, card.anchor_center[0]))
            return Action(
                "play_card",
                params={
                    "card_id": chosen.card_id,
                    "drag_start": chosen.drag_start,
                    "playable_score": chosen.playable_score,
                },
            )
        if board_state.can_end_turn:
            return Action("end_turn")
        return Action("wait")
    if scene == "matching":
        return Action("wait")
    return Action("wait")
