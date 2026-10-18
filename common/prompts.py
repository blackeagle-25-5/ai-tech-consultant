"""Versioned prompts stored as YAML files.

Layout:  <prompts_dir>/<name>/v1.yaml, v2.yaml, ...  plus  <prompts_dir>/<name>/active.txt
Each YAML has: version, system, notes (why this version exists).
Switching `active.txt` back to an older version is the rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    notes: str = ""

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


class PromptRegistry:
    def __init__(self, prompts_dir: Path) -> None:
        self.dir = Path(prompts_dir)

    def versions(self, name: str) -> list[str]:
        files = sorted((self.dir / name).glob("v*.yaml"), key=lambda p: int(p.stem[1:]))
        return [p.stem for p in files]

    def active_version(self, name: str) -> str:
        pointer = self.dir / name / "active.txt"
        if pointer.exists():
            return pointer.read_text(encoding="utf-8").strip()
        versions = self.versions(name)
        if not versions:
            raise FileNotFoundError(f"No prompt versions for '{name}' in {self.dir}")
        return versions[-1]

    def get(self, name: str, version: str | None = None) -> Prompt:
        version = version or self.active_version(name)
        path = self.dir / name / f"{version}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Prompt(name=name, version=version, system=data["system"], notes=data.get("notes", ""))

    def set_active(self, name: str, version: str) -> None:
        if version not in self.versions(name):
            raise ValueError(f"Unknown version {version} for prompt '{name}'")
        (self.dir / name / "active.txt").write_text(version, encoding="utf-8")

    def add_version(self, name: str, system: str, notes: str) -> str:
        existing = self.versions(name)
        version = f"v{len(existing) + 1}"
        path = self.dir / name / f"{version}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"version": version, "notes": notes, "system": system}, sort_keys=False),
            encoding="utf-8",
        )
        return version
