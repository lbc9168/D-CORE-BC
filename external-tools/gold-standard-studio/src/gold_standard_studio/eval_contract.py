"""Load the repository's authoritative eval gold contract without global sys.path mutation."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys


def _repository_root() -> Path:
    """Resolve the monorepo root from the installed or source Studio package location."""
    return Path(__file__).resolve().parents[4]


def _load_repository_eval():
    """Import the local eval package under a private Studio namespace when it is not globally installed."""
    package_name = "gold_standard_studio._repository_eval"
    if package_name not in sys.modules:
        package_dir = _repository_root() / "eval"
        spec = importlib.util.spec_from_file_location(
            package_name, package_dir / "__init__.py", submodule_search_locations=[str(package_dir)]
        )
        if spec is None or spec.loader is None:
            raise ImportError("cannot load repository eval package")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{package_name}.gold_loader")


try:
    from eval.gold_loader import GoldRecord, load as load_projection
except ModuleNotFoundError:
    _gold_loader = _load_repository_eval()
    GoldRecord = _gold_loader.GoldRecord
    load_projection = _gold_loader.load
