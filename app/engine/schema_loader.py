"""Discover trusted project-local Pydantic classes by simple class name."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from pydantic import BaseModel

from app.engine.exceptions import ConfigurationError, SchemaNotFoundError


class SchemaLoader:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.schemas: dict[str, type[BaseModel]] = {}

    def discover(self) -> None:
        schema_root = (self.project_root / "schemas").resolve()
        if not schema_root.is_dir():
            raise ConfigurationError("Missing project/schemas directory")
        for path in sorted(schema_root.glob("*.py")):
            if path.name == "__init__.py":
                continue
            if not path.resolve().is_relative_to(schema_root):
                raise ConfigurationError(f"Schema path escapes schemas/: {path.name}")
            module_name = f"_agent_project_schema_{abs(hash(path.resolve()))}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ConfigurationError(f"Cannot import schema module: {path.name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                del sys.modules[module_name]
                raise ConfigurationError(f"Cannot import schema module: {path.name}") from exc
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ != module_name or not issubclass(cls, BaseModel):
                    continue
                if name in self.schemas:
                    raise ConfigurationError(f"Duplicate schema name: {name}")
                self.schemas[name] = cls

    def get(self, name: str) -> type[BaseModel]:
        try:
            return self.schemas[name]
        except KeyError as exc:
            raise SchemaNotFoundError(f"Unknown schema: {name}") from exc
