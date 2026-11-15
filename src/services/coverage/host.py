"""Ask the hosted service what it holds, so the check compares like with like.

`GET /admin/coverage-probe` runs `probe_connection` on the host's own database and
returns the same `DbProbe` a local file produces. A live call by design: the answer
is the host's state right now, and a cached one would defeat the check.
"""

import httpx

from src.models.coverage import DbProbe


def fetch_host_probe(
    api_url: str,
    api_key: str,
    *,
    tables: list[str],
    listing_table: str,
    jurisdiction: str,
    spot_kinds: list[str],
    bbox: tuple[float, float, float, float] | None,
    client: httpx.Client | None = None,
) -> DbProbe:
    own = client is None
    http = client or httpx.Client(timeout=60)
    params = {
        "tables": ",".join(tables),
        "listing_table": listing_table,
        "jurisdiction": jurisdiction,
        "kinds": ",".join(spot_kinds),
    }
    if bbox:
        params["bbox"] = ",".join(str(v) for v in bbox)
    try:
        resp = http.get(
            api_url.rstrip("/") + "/admin/coverage-probe",
            params=params,
            headers={"X-Api-Key": api_key},
        )
        resp.raise_for_status()
        probe = DbProbe.model_validate(resp.json())
    finally:
        if own:
            http.close()
    probe.source = api_url
    return probe
