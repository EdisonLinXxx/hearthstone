from __future__ import annotations

import argparse
import csv
from pathlib import Path

from bot.config import BASE_DIR, SAMPLES_DIR


FIELDS = ["sample_id", "image_path", "full_path", "meta_path", "label", "label_status"]


def _relative_to_repo(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))


def _load_existing_rows(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists():
        return {}

    existing: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sample_id = row.get("sample_id", "").strip()
            if not sample_id:
                continue
            existing[sample_id] = {field: row.get(field, "") for field in FIELDS}
    return existing


def _build_rows(
    samples_dir: Path,
    pattern: str,
    csv_path: Path,
) -> list[dict[str, str]]:
    existing = _load_existing_rows(csv_path)
    rows: list[dict[str, str]] = []

    for image_path in sorted(samples_dir.glob(pattern)):
        name = image_path.name
        if pattern == "*_mana.png":
            sample_id = name[: -len("_mana.png")]
            base_id = sample_id
        else:
            sample_id = name[: -len(".png")]
            base_id = sample_id.split("_cost_card_")[0]

        existing_row = existing.get(sample_id, {})
        rows.append(
            {
                "sample_id": sample_id,
                "image_path": _relative_to_repo(image_path),
                "full_path": _relative_to_repo(samples_dir / f"{base_id}_full.png"),
                "meta_path": _relative_to_repo(samples_dir / f"{base_id}_meta.txt"),
                "label": existing_row.get("label", ""),
                "label_status": existing_row.get("label_status", ""),
            }
        )
    return rows


def _write_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def build_manifests(asset_profile: str, tag: str) -> tuple[int, int]:
    samples_dir = SAMPLES_DIR / asset_profile / tag
    dataset_dir = BASE_DIR / "bot" / "datasets" / "ocr" / asset_profile

    mana_csv = dataset_dir / "mana_to_label.csv"
    cost_csv = dataset_dir / "cost_to_label.csv"

    mana_rows = _build_rows(samples_dir, "*_mana.png", mana_csv)
    cost_rows = _build_rows(samples_dir, "*_cost_card_*.png", cost_csv)

    _write_rows(mana_csv, mana_rows)
    _write_rows(cost_csv, cost_rows)
    return len(mana_rows), len(cost_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OCR labeling manifests from sampled files.")
    parser.add_argument(
        "--resolution",
        default="1440x900",
        help="Asset profile / sample resolution, for example 1440x900.",
    )
    parser.add_argument(
        "--tag",
        default="ocr_auto_turn_end",
        help="Sample tag directory under bot/samples/<resolution>/.",
    )
    args = parser.parse_args()

    mana_count, cost_count = build_manifests(args.resolution, args.tag)
    print(f"mana={mana_count}")
    print(f"cost={cost_count}")


if __name__ == "__main__":
    main()
