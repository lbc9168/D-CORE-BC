"""Load root-level shared modules without mutating the process-wide import search path."""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
import sys


@lru_cache(maxsize=None)
def load_root_module(name: str):
    """Load one approved root Python module under the current eval package namespace."""
    root = Path(__file__).resolve().parents[1]
    path = root / f"{name}.py"
    if not path.is_file():
        raise ImportError(f"missing root module: {name}")
    module_name = f"{__package__}._root_{name}" if __package__ else f"_eval_root_{name}"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load root module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


bri_enums = load_root_module("bri_enums")
