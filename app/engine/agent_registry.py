"""Agent definitions loaded from project configuration."""

from app.engine.definitions import AgentDefinition
from app.engine.exceptions import AgentNotFoundError


class AgentRegistry:
    def __init__(self, definitions: dict[str, AgentDefinition]) -> None:
        self._definitions = definitions

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise AgentNotFoundError(f"Unknown agent: {name}") from exc

    def exists(self, name: str) -> bool:
        return name in self._definitions

    def all(self) -> dict[str, AgentDefinition]:
        return dict(self._definitions)
