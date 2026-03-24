from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR, SAMPLES_DIR


FIELDS = [
    "sample_id",
    "timestamp",
    "profile",
    "tag",
    "scene",
    "sample_kind",
    "trigger_reason",
    "all_trigger_reasons",
    "hand_source",
    "hand_cards_ready",
    "ocr_trusted",
    "mana_current",
    "mana_total",
    "final_cards_count",
    "debug_candidate_count",
    "ocr_reject_reasons",
    "meta_json_path",
    "full_path",
    "mana_path",
    "hand_path",
]


def _relative_to_repo(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _build_row(meta_path: Path) -> dict[str, str]:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    sample_id = str(payload.get("sample_id", "")).strip() or meta_path.name.removesuffix("_meta.json")
    all_trigger_reasons = _as_list(payload.get("all_trigger_reasons"))
    trigger_reason = str(payload.get("trigger_reason", "")).strip()
    if not trigger_reason and all_trigger_reasons:
        trigger_reason = all_trigger_reasons[0]
    if trigger_reason and trigger_reason not in all_trigger_reasons:
        all_trigger_reasons.insert(0, trigger_reason)

    stem = meta_path.name.removesuffix("_meta.json")
    sample_dir = meta_path.parent
    full_path = sample_dir / f"{stem}_full.png"
    mana_path = sample_dir / f"{stem}_mana.png"
    hand_path = sample_dir / f"{stem}_hand.png"

    return {
        "sample_id": sample_id,
        "timestamp": str(payload.get("timestamp") or payload.get("captured_at") or ""),
        "profile": str(payload.get("profile", "")),
        "tag": str(payload.get("tag", "")),
        "scene": str(payload.get("scene", "")),
        "sample_kind": str(payload.get("sample_kind", "")),
        "trigger_reason": trigger_reason,
        "all_trigger_reasons": "|".join(all_trigger_reasons),
        "hand_source": str(payload.get("hand_source", "")),
        "hand_cards_ready": str(payload.get("hand_cards_ready", "")),
        "ocr_trusted": str(payload.get("ocr_trusted", "")),
        "mana_current": str(payload.get("mana_current", "")),
        "mana_total": str(payload.get("mana_total", "")),
        "final_cards_count": str(payload.get("final_cards_count", "")),
        "debug_candidate_count": str(payload.get("debug_candidate_count", "")),
        "ocr_reject_reasons": "|".join(_as_list(payload.get("ocr_reject_reasons"))),
        "meta_json_path": _relative_to_repo(meta_path),
        "full_path": _relative_to_repo(full_path) if full_path.exists() else "",
        "mana_path": _relative_to_repo(mana_path) if mana_path.exists() else "",
        "hand_path": _relative_to_repo(hand_path) if hand_path.exists() else "",
    }


def build_anomaly_manifest(asset_profile: str, tag: str, output: Path | None = None) -> tuple[int, Path]:
    samples_dir = SAMPLES_DIR / asset_profile / tag
    output_path = output or (samples_dir / "anomaly_index.csv")
    rows = [_build_row(path) for path in sorted(samples_dir.glob("*_meta.json"))]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a lightweight index for OCR anomaly samples.")
    parser.add_argument("--resolution", default="1440x900", help="Asset profile / sample resolution, for example 1440x900.")
    parser.add_argument("--tag", default="ocr_anomaly", help="Sample tag directory under bot/samples/<resolution>/.")
    parser.add_argument("--output", default="", help="Optional output CSV path. Defaults to <samples_dir>/anomaly_index.csv")
    args = parser.parse_args()

    output = Path(args.output).resolve() if args.output else None
    count, output_path = build_anomaly_manifest(args.resolution, args.tag, output=output)
    print(f"rows={count}")
    print(f"output={output_path}")


if __name__ == "__main__":
    main()
