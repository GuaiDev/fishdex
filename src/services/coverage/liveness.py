"""Liveness: is the hosted service reachable from outside?

One machine's answer proves little: a host can be fine from the developer's network
and dead from everywhere else (or the reverse, behind a TLS-inspecting proxy). The
check asks check-host.net, which probes from several outside vantage points and
needs no account.

This is a live probe by design, so it does not go through the response cache: a
cached "up" is exactly the wrong answer to "is it up".
"""

import time

import httpx

from src.models.coverage import LivenessReport, VantageResult

CHECK_HOST = "https://check-host.net"
_HEADERS = {"Accept": "application/json", "User-Agent": "fishbot/1.0 (coverage check)"}


def probe_outside(
    base_url: str,
    path: str,
    required: int,
    *,
    client: httpx.Client | None = None,
    wait_s: float = 12.0,
    poll_s: float = 2.0,
    sleep=time.sleep,
) -> LivenessReport:
    """Ask check-host.net to fetch `base_url + path` from `required` or more nodes."""
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    own = client is None
    http = client or httpx.Client(headers=_HEADERS, timeout=30)
    try:
        start = http.get(
            f"{CHECK_HOST}/check-http",
            params={"host": url, "max_nodes": max(required, 3)},
            headers=_HEADERS,
        )
        start.raise_for_status()
        body = start.json()
        request_id = body.get("request_id")
        nodes = list(body.get("nodes", {}))
        if not request_id or not nodes:
            raise RuntimeError(f"check-host.net did not start a check: {body}")
        waited = 0.0
        raw: dict = {}
        while waited <= wait_s:
            res = http.get(f"{CHECK_HOST}/check-result/{request_id}", headers=_HEADERS)
            res.raise_for_status()
            raw = res.json()
            if all(raw.get(n) is not None for n in nodes):
                break
            sleep(poll_s)
            waited += poll_s
        results = [parse_node(n, raw.get(n)) for n in nodes]
    finally:
        if own:
            http.close()
    return LivenessReport(
        url=url, required=required, vantage_points=results, prober="check-host.net"
    )


def parse_node(node: str, payload) -> VantageResult:
    """One node's check-host.net answer: [[ok, seconds, message, code, ip]] or null."""
    if payload is None:
        return VantageResult(node=node, ok=False, detail="no answer in time")
    try:
        attempt = payload[0]
        if not attempt:
            return VantageResult(node=node, ok=False, detail="no answer in time")
        ok = bool(attempt[0])
        message = str(attempt[2]) if len(attempt) > 2 else ""
        code = int(attempt[3]) if len(attempt) > 3 and str(attempt[3]).isdigit() else None
        return VantageResult(node=node, ok=ok and code == 200, status=code, detail=message)
    except (TypeError, IndexError, ValueError) as exc:
        return VantageResult(node=node, ok=False, detail=f"unreadable answer: {exc}")
