"""Load a jurisdiction's reference registry (`data/coverage/references_<code>.toml`)."""

import tomllib
from pathlib import Path

from src.models.coverage import CoverageRegistry

REGISTRY_DIR = Path("data/coverage")


def registry_path(jurisdiction: str, directory: Path | None = None) -> Path:
    """`CA-ON` -> `data/coverage/references_ca_on.toml`."""
    slug = jurisdiction.lower().replace("-", "_")
    return (directory or REGISTRY_DIR) / f"references_{slug}.toml"


def load_registry(path: Path) -> CoverageRegistry:
    """Read and validate a registry file. A missing file says which jurisdiction needs one."""
    if not path.exists():
        raise FileNotFoundError(
            f"No coverage registry at {path}. A new jurisdiction needs one: copy "
            "data/coverage/references_ca_on.toml and change the layers."
        )
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return CoverageRegistry.model_validate(raw)
