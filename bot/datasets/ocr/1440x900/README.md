# OCR Dataset Notes

Current manifests:

- `mana_to_label.csv`: label current mana / total mana text such as `3/3`
- `cost_to_label.csv`: obsolete, earlier hand-cost crops proved meaningless and should not be expanded

Launch tools:

- `python -m bot.ocr_label_mana_main`
- `python -m bot.ocr_label_cost_main`
- `python -m bot.ocr_label_main --csv ...` remains available as a compatibility entrypoint

Sampling notes:

- Early `mana_to_label.csv` rows may point at the wrong crop area from older auto-sampling runs.
- New auto-sampling now targets the bottom-right in-match mana bar region.
- `cost_to_label.csv` should be treated as historical noise unless hand-cost sampling is redesigned.

Shared columns:

- `sample_id`: sample group id
- `image_path`: source mana image path
- `meta_path`: source metadata path
- `label`: fill in the ground-truth text
- `label_status`: use values like `pending`, `done`, or `skip`

Suggested workflow:

1. Re-sample with `python -m bot.main --deck-index 1 --resolution 1440x900 --ocr-auto-sample`
2. Open each `image_path`
3. Use `full_path` as context when needed
4. Fill `label`
5. Change `label_status` from `pending` to `done`
