"""Phase 0 tests: configuration syntax and local structural contracts only."""

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from app.engine.definitions import (
    AgentsConfig,
    ModelsConfig,
    ProjectConfig,
    ToolsConfig,
    WorkflowConfig,
    WorkflowDefinition,
)

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@pytest.mark.parametrize(
    ("filename", "contract"),
    [
        ("project.yaml", ProjectConfig),
        ("models.yaml", ModelsConfig),
        ("agents.yaml", AgentsConfig),
        ("tools.yaml", ToolsConfig),
        ("workflow.yaml", WorkflowConfig),
    ],
)
def test_demo_configuration_matches_contract(filename: str, contract: type[BaseModel]) -> None:
    data = yaml.safe_load((PROJECT_DIR / filename).read_text(encoding="utf-8"))
    contract.model_validate(data)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelsConfig.model_validate(
            {
                "schema_version": 1,
                "models": {"default": {"provider": "openai", "model": "demo", "typo": 1}},
            }
        )


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({"schema_version": 2, "project": {"name": "test"}})


def test_invalid_start_node_is_rejected() -> None:
    data = yaml.safe_load((PROJECT_DIR / "workflow.yaml").read_text(encoding="utf-8"))
    data["workflow"]["start"] = "missing"
    with pytest.raises(ValidationError, match="Start node"):
        WorkflowConfig.model_validate(data)


def test_invalid_transition_destination_is_rejected() -> None:
    data = yaml.safe_load((PROJECT_DIR / "workflow.yaml").read_text(encoding="utf-8"))
    data["workflow"]["transitions"][0]["to"] = "missing"
    with pytest.raises(ValidationError, match="Unknown transition destination"):
        WorkflowConfig.model_validate(data)


@pytest.mark.parametrize("bad_prompt", ["/tmp/outside.md", "../outside.md", "a/../outside.md"])
def test_prompt_path_cannot_escape_project(bad_prompt: str) -> None:
    data = yaml.safe_load((PROJECT_DIR / "agents.yaml").read_text(encoding="utf-8"))
    data["agents"]["analyzer"]["prompt"] = bad_prompt
    with pytest.raises(ValidationError):
        AgentsConfig.model_validate(data)


def test_conditional_transition_contract() -> None:
    data = yaml.safe_load((PROJECT_DIR / "workflow.yaml").read_text(encoding="utf-8"))
    data["workflow"]["transitions"][1] = {
        "from": "review",
        "select": "results.review.output.status",
        "cases": {"approved": "finalize", "rejected": "analyze"},
        "default": "fail",
    }
    parsed = WorkflowConfig.model_validate(data)
    assert isinstance(parsed.workflow, WorkflowDefinition)
    assert parsed.workflow.transitions[1].from_node == "review"


def test_parallel_node_contract() -> None:
    data = yaml.safe_load((PROJECT_DIR / "workflow.yaml").read_text(encoding="utf-8"))
    data["workflow"]["nodes"]["review"] = {
        "type": "parallel",
        "branches": {"first": {"agent": "reviewer"}, "second": {"agent": "analyzer"}},
    }
    parsed = WorkflowConfig.model_validate(data)
    assert isinstance(parsed.workflow, WorkflowDefinition)
    assert parsed.workflow.nodes["review"].type == "parallel"


def test_tool_root_cannot_escape_project() -> None:
    with pytest.raises(ValidationError):
        ToolsConfig.model_validate(
            {
                "schema_version": 1,
                "tools": {
                    "files": {"type": "builtin", "implementation": "filesystem", "root": "../"}
                },
            }
        )


def test_orchestrator_specialist_tool_names_must_not_collide() -> None:
    with pytest.raises(ValidationError, match="unique after normalization"):
        WorkflowConfig.model_validate(
            {
                "schema_version": 1,
                "workflow": {
                    "name": "managed", "type": "orchestrator", "manager": "lead",
                    "specialists": ["a-b", "a_b"],
                },
            }
        )
