from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
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

BOOL_FIELDS = {"hand_cards_ready", "ocr_trusted"}
INT_FIELDS = {"mana_current", "mana_total", "final_cards_count", "debug_candidate_count"}
LIST_FIELDS = {"all_trigger_reasons", "ocr_reject_reasons"}


def _relative_to_repo(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if "|" in text:
        return [item.strip() for item in text.split("|") if item.strip()]
    return [text]


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _normalize_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


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


def _load_rows(asset_profile: str, tag: str) -> tuple[list[dict[str, str]], Path]:
    samples_dir = SAMPLES_DIR / asset_profile / tag
    rows = [_build_row(path) for path in sorted(samples_dir.glob("*_meta.json"))]
    return rows, samples_dir


def build_anomaly_manifest(asset_profile: str, tag: str, output: Path | None = None) -> tuple[int, Path]:
    rows, samples_dir = _load_rows(asset_profile, tag)
    output_path = output or (samples_dir / "anomaly_index.csv")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), output_path


def _match_scalar(row: dict[str, str], field: str, expected: str) -> bool:
    actual = row.get(field, "")
    if field in BOOL_FIELDS:
        expected_bool = _normalize_bool(expected)
        actual_bool = _normalize_bool(actual)
        if expected_bool is not None and actual_bool is not None:
            return actual_bool == expected_bool
    if field in INT_FIELDS:
        expected_int = _normalize_int(expected)
        actual_int = _normalize_int(actual)
        if expected_int is not None and actual_int is not None:
            return actual_int == expected_int
    return actual == expected


def _match_contains(row: dict[str, str], field: str, expected: str) -> bool:
    values = _as_list(row.get(field, "")) if field in LIST_FIELDS else [row.get(field, "")]
    return expected in values


def _apply_filters(rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    filtered = list(rows)

    scalar_filters = {
        "trigger_reason": args.trigger_reason,
        "hand_source": args.hand_source,
        "scene": args.scene,
    }
    for field, expected in scalar_filters.items():
        if expected:
            filtered = [row for row in filtered if _match_scalar(row, field, expected)]

    if args.ocr_trusted != "":
        filtered = [row for row in filtered if _match_scalar(row, "ocr_trusted", args.ocr_trusted)]
    if args.hand_cards_ready != "":
        filtered = [row for row in filtered if _match_scalar(row, "hand_cards_ready", args.hand_cards_ready)]

    if args.has_trigger_reason:
        filtered = [row for row in filtered if _match_contains(row, "all_trigger_reasons", args.has_trigger_reason)]
    if args.has_reject_reason:
        filtered = [row for row in filtered if _match_contains(row, "ocr_reject_reasons", args.has_reject_reason)]

    if args.final_cards_eq is not None:
        filtered = [row for row in filtered if _normalize_int(row.get("final_cards_count")) == args.final_cards_eq]
    if args.final_cards_gt is not None:
        filtered = [row for row in filtered if (_normalize_int(row.get("final_cards_count")) or 0) > args.final_cards_gt]
    if args.debug_candidates_eq is not None:
        filtered = [row for row in filtered if _normalize_int(row.get("debug_candidate_count")) == args.debug_candidates_eq]
    if args.debug_candidates_gt is not None:
        filtered = [row for row in filtered if (_normalize_int(row.get("debug_candidate_count")) or 0) > args.debug_candidates_gt]

    if args.only_zero_final_with_debug:
        filtered = [
            row
            for row in filtered
            if (_normalize_int(row.get("final_cards_count")) or 0) == 0
            and (_normalize_int(row.get("debug_candidate_count")) or 0) > 0
        ]

    filtered.sort(key=lambda row: row.get("timestamp", ""), reverse=not args.oldest_first)
    if args.limit > 0:
        filtered = filtered[: args.limit]
    return filtered


def _print_rows(rows: list[dict[str, str]], fields: list[str]) -> None:
    if not rows:
        print("rows=0")
        return
    print("\t".join(fields))
    for row in rows:
        print("\t".join(str(row.get(field, "")) for field in fields))
    print(f"rows={len(rows)}")


def _counter_from_rows(rows: list[dict[str, str]], field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        values = _as_list(row.get(field, "")) if field in LIST_FIELDS else [row.get(field, "")]
        for value in values:
            if value:
                counter[value] += 1
    return counter


def _print_stats(rows: list[dict[str, str]], *, top: int) -> None:
    print(f"stats_rows={len(rows)}")
    if not rows:
        return
    for field in ["trigger_reason", "all_trigger_reasons", "hand_source", "scene", "ocr_reject_reasons"]:
        counter = _counter_from_rows(rows, field)
        print(f"[{field}]")
        if not counter:
            print("(empty)")
            continue
        for key, count in counter.most_common(top):
            print(f"{count}\t{key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and inspect a lightweight index for OCR anomaly samples.")
    parser.add_argument("--resolution", default="1440x900", help="Asset profile / sample resolution, for example 1440x900.")
    parser.add_argument("--tag", default="ocr_anomaly", help="Sample tag directory under bot/samples/<resolution>/.")
    parser.add_argument("--output", default="", help="Optional output CSV path. Defaults to <samples_dir>/anomaly_index.csv")
    parser.add_argument("--skip-build", action="store_true", help="Do not rewrite CSV; only inspect existing meta.json files.")

    parser.add_argument("--trigger-reason", default="", help="Filter rows whose primary trigger_reason equals this value.")
    parser.add_argument("--has-trigger-reason", default="", help="Filter rows whose all_trigger_reasons contains this value.")
    parser.add_argument("--hand-source", default="", help="Filter rows by hand_source.")
    parser.add_argument("--scene", default="", help="Filter rows by scene.")
    parser.add_argument("--ocr-trusted", default="", help="Filter rows by ocr_trusted=true/false.")
    parser.add_argument("--hand-cards-ready", default="", help="Filter rows by hand_cards_ready=true/false.")
    parser.add_argument("--has-reject-reason", default="", help="Filter rows whose ocr_reject_reasons contains this value.")
    parser.add_argument("--final-cards-eq", type=int, default=None, help="Filter rows by final_cards_count == N.")
    parser.add_argument("--final-cards-gt", type=int, default=None, help="Filter rows by final_cards_count > N.")
    parser.add_argument("--debug-candidates-eq", type=int, default=None, help="Filter rows by debug_candidate_count == N.")
    parser.add_argument("--debug-candidates-gt", type=int, default=None, help="Filter rows by debug_candidate_count > N.")
    parser.add_argument(
        "--only-zero-final-with-debug",
        action="store_true",
        help="Shortcut for final_cards_count == 0 and debug_candidate_count > 0.",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max rows to print when using inspect/stats mode. Use 0 for all.")
    parser.add_argument("--oldest-first", action="store_true", help="Print oldest rows first instead of newest first.")
    parser.add_argument(
        "--fields",
        default="timestamp,sample_id,trigger_reason,hand_source,ocr_trusted,final_cards_count,debug_candidate_count,ocr_reject_reasons,meta_json_path",
        help="Comma-separated fields to print in inspect mode.",
    )
    parser.add_argument("--stats", action="store_true", help="Print summary counters for the filtered rows.")
    parser.add_argument("--stats-top", type=int, default=10, help="Top N items for each counter in stats mode.")
    args = parser.parse_args()

    output = Path(args.output).resolve() if args.output else None
    if not args.skip_build:
        count, output_path = build_anomaly_manifest(args.resolution, args.tag, output=output)
        print(f"rows={count}")
        print(f"output={output_path}")
    else:
        rows, samples_dir = _load_rows(args.resolution, args.tag)
        output_path = output or (samples_dir / "anomaly_index.csv")
        print(f"rows={len(rows)}")
        print(f"output={output_path}")

    has_filter = any(
        [
            args.trigger_reason,
            args.has_trigger_reason,
            args.hand_source,
            args.scene,
            args.ocr_trusted != "",
            args.hand_cards_ready != "",
            args.has_reject_reason,
            args.final_cards_eq is not None,
            args.final_cards_gt is not None,
            args.debug_candidates_eq is not None,
            args.debug_candidates_gt is not None,
            args.only_zero_final_with_debug,
            args.stats,
        ]
    )
    if not has_filter:
        return

    rows, _ = _load_rows(args.resolution, args.tag)
    filtered = _apply_filters(rows, args)
    print(f"filtered_rows={len(filtered)}")

    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    _print_rows(filtered, fields)
    if args.stats:
        _print_stats(filtered, top=args.stats_top)


if __name__ == "__main__":
    main()
