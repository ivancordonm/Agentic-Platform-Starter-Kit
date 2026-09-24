"""Project-local Markdown prompt loading and strict rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

from app.engine.exceptions import ConfigurationError, PromptNotFoundError


class PromptLoader:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.prompt_root = (self.project_root / "prompts").resolve()
        self.environment = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
        self._validated: dict[str, str] = {}

    def load(self, relative_path: str) -> str:
        if relative_path in self._validated:
            return self._validated[relative_path]
        path = (self.project_root / relative_path).resolve()
        if not path.is_relative_to(self.prompt_root) or path.suffix != ".md":
            raise ConfigurationError(f"Prompt path must point inside prompts/: {relative_path}")
        try:
            if path.stat().st_size > 128_000:
                raise ConfigurationError(f"Prompt exceeds 128 KB: {relative_path}")
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PromptNotFoundError(f"Prompt not readable: {relative_path}") from exc

    def render(self, relative_path: str, variables: dict[str, Any]) -> str:
        try:
            return self.environment.from_string(self.load(relative_path)).render(**variables)
        except TemplateError as exc:
            raise ConfigurationError(f"Cannot render prompt {relative_path}: {exc}") from exc

    def validate(self, relative_path: str) -> None:
        allowed = {"project_name", "input", "context", "results", "messages"}
        source = self.load(relative_path)
        try:
            parsed = self.environment.parse(source)
            unknown = meta.find_undeclared_variables(parsed) - allowed
        except TemplateError as exc:
            raise ConfigurationError(f"Invalid prompt {relative_path}: {exc}") from exc
        if unknown:
            raise ConfigurationError(
                f"Prompt {relative_path} uses unknown variables: {', '.join(sorted(unknown))}"
            )
        # Freeze trusted prompt text for the lifetime of this runtime revision.
        self._validated[relative_path] = source
