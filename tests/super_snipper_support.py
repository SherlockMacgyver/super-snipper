"""Test access to the ``supersnipper`` package.

``load()`` re-executes every package module so each test starts from fresh
module state, as the old single-file loader did. It returns a ``Snipper``
object that routes each attribute to the one module that defines it: reading
``snipper.record_video`` returns ``recording.record_video``, and
``patch.object(snipper, "virtual_screen", ...)`` replaces ``winapi``'s binding,
which is the one every caller looks up. A name no module defines raises
AttributeError instead of silently patching a copy nobody calls.
"""

from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
LAUNCHER = SCRIPTS / "super_snipper.py"
PACKAGE = SCRIPTS / "supersnipper"
# Dependency order: each module is reloaded after everything it imports.
MODULES = ("config", "winapi", "overlays", "capture", "recording", "docking",
           "markup", "hud_widget", "panel", "cli")


def _defined_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _owners() -> dict[str, str]:
    owners: dict[str, str] = {}
    for module in MODULES:
        for name in _defined_names(PACKAGE / f"{module}.py"):
            if name in owners:
                raise RuntimeError(f"{name!r} is defined in both {owners[name]} and {module}")
            owners[name] = module
    return owners


class Snipper:
    def __init__(self, modules: dict[str, types.ModuleType]) -> None:
        object.__setattr__(self, "_modules", modules)
        object.__setattr__(self, "_owners", _owners())

    def _owner(self, name: str) -> types.ModuleType:
        try:
            return self._modules[self._owners[name]]
        except KeyError:
            raise AttributeError(f"no supersnipper module defines {name!r}") from None

    def __getattr__(self, name: str):
        if name in self._modules:
            return self._modules[name]
        if name in self._owners:
            return getattr(self._owner(name), name)
        # Read-only access to an imported library module, e.g. snipper.subprocess.
        for module in self._modules.values():
            value = getattr(module, name, None)
            if isinstance(value, types.ModuleType):
                return value
        raise AttributeError(f"no supersnipper module defines {name!r}")

    def __setattr__(self, name: str, value) -> None:
        setattr(self._owner(name), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._owner(name), name)


def load() -> Snipper:
    """Fresh package state; set SUPER_SNIPPER_HOME/INSTANCE env vars first."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    modules = {}
    for name in MODULES:
        full = f"supersnipper.{name}"
        module = sys.modules.get(full)
        modules[name] = importlib.reload(module) if module else importlib.import_module(full)
    return Snipper(modules)
