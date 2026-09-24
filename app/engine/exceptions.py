"""Errors that can be mapped to stable API responses."""


class PlatformError(Exception):
    """Base exception for project and runtime errors."""


class ConfigurationError(PlatformError):
    """A project file is invalid or a reference cannot be resolved."""


class AgentNotFoundError(PlatformError):
    """An agent identifier does not exist."""


class ModelNotFoundError(ConfigurationError):
    """An agent references a missing model."""


class ToolNotFoundError(ConfigurationError):
    """An agent references a missing tool."""


class PromptNotFoundError(ConfigurationError):
    """An agent references a missing prompt."""


class SchemaNotFoundError(ConfigurationError):
    """An agent references a missing Pydantic schema."""


class AgentExecutionError(PlatformError):
    """An agent run failed after configuration succeeded."""


class AgentInputError(AgentExecutionError):
    """The caller input did not match the agent input schema."""


class WorkflowExecutionError(PlatformError):
    """A compiled workflow could not complete or select its output."""
