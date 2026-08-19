"""HTTP API entry points.

Keep this package import lightweight.  The desktop Runtime must not load the
legacy Capability API (and its Agent/browser dependencies) merely to expose
the Runtime health endpoint.
"""

from importlib import import_module
from typing import Any

__all__ = ["app", "create_app", "create_runtime_app"]


def __getattr__(name: str) -> Any:
    if name in {"app", "create_app"}:
        module = import_module("app.api.capabilities")
        return getattr(module, name)
    if name == "create_runtime_app":
        return import_module("app.api.runtime").create_runtime_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
