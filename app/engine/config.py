"""Safe loading of the five versioned project configuration files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.engine.definitions import (
    AgentsConfig,
    ModelsConfig,
    ProjectConfig,
    ToolsConfig,
    WorkflowConfig,
)
from app.engine.exceptions import ConfigurationError


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that fails instead of silently overriding YAML keys."""


def _construct_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ConfigurationError(
                f"Duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)

@dataclass(frozen=True)
class ProjectConfigs:
    root: Path
    project: ProjectConfig
    models: ModelsConfig
    agents: AgentsConfig
    tools: ToolsConfig
    workflow: WorkflowConfig


def _read_yaml[TConfig: BaseModel](path: Path, contract: type[TConfig]) -> TConfig:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        return contract.model_validate(raw)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Missing configuration file: {path.name}") from exc
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValidationError,
        ConfigurationError,
        TypeError,
    ) as exc:
        raise ConfigurationError(f"Invalid {path.name}: {exc}") from exc


def load_project_configs(root: Path) -> ProjectConfigs:
    root = root.resolve()
    if not root.is_dir():
        raise ConfigurationError(f"Project directory does not exist: {root}")
    return ProjectConfigs(
        root=root,
        project=_read_yaml(root / "project.yaml", ProjectConfig),
        models=_read_yaml(root / "models.yaml", ModelsConfig),
        agents=_read_yaml(root / "agents.yaml", AgentsConfig),
        tools=_read_yaml(root / "tools.yaml", ToolsConfig),
        workflow=_read_yaml(root / "workflow.yaml", WorkflowConfig),
    )
