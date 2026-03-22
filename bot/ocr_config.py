from __future__ import annotations

from dataclasses import dataclass

from bot.loader import load_yaml


@dataclass(frozen=True)
class OcrRegionConfig:
    scale: int
    grayscale: bool
    threshold: int
    invert: bool
    whitelist: str


def load_ocr_config(path) -> dict[str, OcrRegionConfig]:
    data = load_yaml(path)
    configs: dict[str, OcrRegionConfig] = {}
    for name, value in data.items():
        configs[name] = OcrRegionConfig(
            scale=int(value["scale"]),
            grayscale=bool(value["grayscale"]),
            threshold=int(value["threshold"]),
            invert=bool(value["invert"]),
            whitelist=str(value["whitelist"]),
        )
    return configs
