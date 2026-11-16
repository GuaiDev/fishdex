"""The one registry of jurisdictions known to the bot.

A jurisdiction exists because its folder under `src/ingest/jurisdictions/`
exports a `JurisdictionPlugin` as `PLUGIN` from `plugin.py`. This module finds
those folders; nothing else lists regions, so adding one touches only its own
folder. See `src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md`.

`get_jurisdiction(code)` returns a populated `Jurisdiction` if the code is
known, otherwise an `UnknownJurisdiction(code)` so the bot still works
gracefully — just with a "limited data" disclaimer in its system prompt.
"""

import importlib
import logging
import pkgutil
from functools import lru_cache

from src.jurisdictions.base import Jurisdiction
from src.jurisdictions.plugin import GLOBAL_SOURCE, BBox, JurisdictionPlugin
from src.jurisdictions.unknown import UnknownJurisdiction

logger = logging.getLogger(__name__)

PLUGIN_PACKAGE = "src.ingest.jurisdictions"
__all__ = ["GLOBAL_SOURCE", "get_jurisdiction", "get_plugin", "all_plugins", "plugins"]


def _code_for_folder(folder: str) -> str:
    """ca_on -> CA-ON. Folders without a "<country>_<subdivision>" name are not regions."""
    country, _, subdivision = folder.partition("_")
    if len(country) != 2 or not 1 <= len(subdivision) <= 3 or "_" in subdivision:
        return ""
    return f"{country.upper()}-{subdivision.upper()}"


@lru_cache(maxsize=1)
def plugins() -> dict[str, JurisdictionPlugin]:
    """Every plugin, by code. Raises if a folder's plugin is broken or mislabelled.

    A plugin that fails to import is an error rather than a skipped region:
    a region that silently vanished would be tagged UNKNOWN by every adapter.
    """
    package = importlib.import_module(PLUGIN_PACKAGE)
    found: dict[str, JurisdictionPlugin] = {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        code = _code_for_folder(info.name)
        if not info.ispkg or not code:
            continue
        try:
            module = importlib.import_module(f"{PLUGIN_PACKAGE}.{info.name}.plugin")
        except ModuleNotFoundError as exc:
            if exc.name != f"{PLUGIN_PACKAGE}.{info.name}.plugin":
                raise
            logger.warning("jurisdiction folder %s has no plugin.py; not registered", info.name)
            continue
        plugin = module.PLUGIN
        if plugin.code != code:
            raise ValueError(f"{info.name}/plugin.py declares {plugin.code!r}, expected {code!r}")
        if plugin.code in found:
            raise ValueError(f"jurisdiction {plugin.code} is declared twice")
        found[plugin.code] = plugin
    return found


def reload_plugins() -> None:
    """Forget what was discovered; the next call scans the folders again."""
    plugins.cache_clear()
    bboxes.cache_clear()


def get_plugin(code: str) -> JurisdictionPlugin | None:
    return plugins().get(code)


def all_plugins() -> list[JurisdictionPlugin]:
    return list(plugins().values())


def get_jurisdiction(code: str) -> Jurisdiction:
    plugin = plugins().get(code)
    if plugin is None:
        return UnknownJurisdiction(code=code)
    return Jurisdiction(
        code=plugin.code,
        name=plugin.name,
        country=plugin.country,
        has_detailed_data=plugin.has_detailed_data,
        context=plugin.regulatory_context,
    )


@lru_cache(maxsize=1)
def bboxes() -> tuple[tuple[str, BBox], ...]:
    """(code, box) pairs in lookup order, for coordinate tagging."""
    boxed = [(p.code, p.bbox) for p in plugins().values() if p.bbox is not None]
    return tuple(sorted(boxed, key=lambda cb: (cb[1].priority, cb[0])))
