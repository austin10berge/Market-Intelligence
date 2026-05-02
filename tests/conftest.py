"""pytest configuration — stubs heavy optional dependencies before any test module imports."""

from __future__ import annotations

import sys
import types


def _stub_module(name: str) -> None:
    """Insert a lightweight stub into sys.modules if the real module is absent."""
    if name not in sys.modules:
        try:
            __import__(name)
        except ModuleNotFoundError:
            stub = types.ModuleType(name)
            sys.modules[name] = stub
            parts = name.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                if parent not in sys.modules:
                    sys.modules[parent] = types.ModuleType(parent)


_stub_module("pandas_ta")
