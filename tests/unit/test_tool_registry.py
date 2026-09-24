"""Filesystem tool confinement tests."""

from pathlib import Path

from app.engine.definitions import BuiltinToolDefinition
from app.engine.tool_registry import ToolRegistry, read_project_file


def test_filesystem_tool_resolves_from_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "knowledge").mkdir(parents=True)
    registry = ToolRegistry(
        root, {"files": BuiltinToolDefinition(type="builtin", implementation="filesystem")}
    )
    assert registry.resolve("files").name == "files_read_file"


def test_filesystem_reader_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "inside.md").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.md").symlink_to(outside)
    assert read_project_file(root, "inside.md") == "safe"
    assert read_project_file(root, "../outside.md") != "secret"
    assert read_project_file(root, "link.md") != "secret"
    assert read_project_file(root, str(outside)) == "Invalid path"
