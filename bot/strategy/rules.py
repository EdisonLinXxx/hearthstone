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
    if scene == "match_error":
        return Action("confirm_match_error")
    if scene == "result_continue":
        return Action("continue_result")
    if scene == "result":
        return Action("confirm_result")
    if scene == "battle":
        if board_state is None or not board_state.my_turn:
            return Action("wait")
        if not board_state.hand_cards_ready:
            return Action("battle_wait", params={"reason": f"hand_cards_not_ready:{board_state.hand_source}"})
        if not board_state.ocr_trusted:
            reject_reason = ",".join(board_state.ocr_reject_reasons[:3]) if board_state.ocr_reject_reasons else board_state.hand_source
            return Action("battle_wait", params={"reason": f"ocr_untrusted:{reject_reason}"})
        attempted_cards = attempted_cards or set()
        available_cards = [
            card
            for card in board_state.hand_cards
            if card.card_id not in attempted_cards and card.playable
        ]
        if available_cards:
            if any(card.mana_cost is not None for card in available_cards):
                chosen = min(
                    available_cards,
                    key=lambda card: (
                        card.mana_cost if card.mana_cost is not None else 99,
                        -card.ocr_confidence,
                        card.anchor_center[0],
                    ),
                )
            else:
                return Action("wait")
            return Action(
                "play_card",
                params={
                    "card_id": chosen.card_id,
                    "drag_start": chosen.drag_start,
                    "ocr_confidence": chosen.ocr_confidence,
                    "mana_cost": chosen.mana_cost,
                },
            )
        if board_state.can_end_turn:
            return Action("end_turn")
        return Action("wait")
    if scene == "matching":
        return Action("wait")
    return Action("wait")
