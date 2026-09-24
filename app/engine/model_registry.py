"""Provider-neutral logical model registry, with an OpenAI SDK adapter."""

from __future__ import annotations

from agents import ModelSettings
from openai.types.shared import Reasoning

from app.engine.definitions import ModelDefinition
from app.engine.exceptions import ConfigurationError, ModelNotFoundError


class ModelRegistry:
    def __init__(self, definitions: dict[str, ModelDefinition]) -> None:
        self._definitions = definitions

    def get(self, name: str) -> ModelDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ModelNotFoundError(f"Unknown model: {name}") from exc

    def resolve_openai(self, name: str) -> tuple[str, ModelSettings]:
        definition = self.get(name)
        if definition.provider != "openai":
            raise ConfigurationError(
                f"Model {name!r} uses unsupported provider {definition.provider!r}"
            )
        reasoning = Reasoning(effort=definition.reasoning.effort) if definition.reasoning else None
        return definition.model, ModelSettings(
            temperature=definition.temperature,
            reasoning=reasoning,
        )

    def all(self) -> dict[str, ModelDefinition]:
        return dict(self._definitions)
