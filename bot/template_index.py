from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bot.loader import load_yaml


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    path: Path
    threshold: float


def load_template_specs(index_path: Path, templates_dir: Path) -> dict[str, TemplateSpec]:
    data = load_yaml(index_path)
    templates = data.get("templates", {})
    specs: dict[str, TemplateSpec] = {}
    for name, value in templates.items():
        specs[name] = TemplateSpec(
            name=name,
            path=templates_dir / value["file"],
            threshold=float(value["threshold"]),
        )
    return specs
