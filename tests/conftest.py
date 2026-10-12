"""Register the repository root as a real package, including its initializer."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if "nekro_mc_status" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "nekro_mc_status", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
