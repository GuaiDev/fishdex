"""The coverage check: does the map hold what the authorities hold?

Region-agnostic. A region is a registry file (`data/coverage/references_*.toml`)
plus a jurisdiction code; the ladder, metrics and gates are geometry-only.
"""

from src.services.coverage.registry import load_registry, registry_path
from src.services.coverage.render import to_json, to_markdown, write_reports
from src.services.coverage.runner import run_coverage_check

__all__ = [
    "load_registry",
    "registry_path",
    "run_coverage_check",
    "to_json",
    "to_markdown",
    "write_reports",
]
