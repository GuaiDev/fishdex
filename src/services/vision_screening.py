"""Claude vision pre-screening for stream segment candidates.

Fetches a Mapbox satellite tile for each candidate and asks Claude to verify:
  1. Is there visible open water?
  2. Is it natural stream or engineered drainage?
  3. Are there structures blocking bank access?
  4. Are there structural features (confluence, widening, culvert)?
"""

import base64
import logging
import os

import httpx
from dotenv import load_dotenv

from src import llm

load_dotenv()
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")

logger = logging.getLogger(__name__)


def fetch_satellite_tile(
    lat: float,
    lng: float,
    zoom: int = 17,
    width: int = 600,
    height: int = 400,
) -> bytes | None:
    """Fetch Mapbox satellite tile as PNG bytes."""
    url = (
        f"https://api.mapbox.com/styles/v1/"
        f"mapbox/satellite-v9/static/"
        f"{lng},{lat},{zoom},0/"
        f"{width}x{height}"
        f"?access_token={MAPBOX_TOKEN}"
    )
    try:
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            return r.content
        return None
    except Exception:
        return None


def screen_segment(
    lat: float,
    lng: float,
    stream_order: int,
    watercourse_name: str | None,
    is_confluence: bool,
    *,
    user_id: int | None,
) -> dict:
    """Analyze a satellite tile with Claude vision. Returns screening result dict."""
    tile = fetch_satellite_tile(lat, lng)
    if tile is None:
        return {
            "screened": False,
            "reason": "satellite image unavailable",
            "verdict": "unverified",
            "vision_note": None,
        }

    image_data = base64.standard_b64encode(tile).decode()

    context = f"Stream order {stream_order}"
    if watercourse_name:
        context += f", {watercourse_name}"
    if is_confluence:
        context += ", confluence point"

    prompt = f"""Analyze this satellite image of a stream location \
({context}) and answer these questions concisely:

1. WATER: Is there clearly visible open water? (yes/no/unclear)
2. TYPE: Does it look like natural stream, engineered drainage/ditch, \
culvert crossing, or pond/lake? (one of these)
3. ACCESS: Is access likely blocked by any of these? Houses/residential \
buildings directly on the bank, golf course or sports facility, fenced \
private property, manicured lawns extending to the water. \
(yes/no/partial — specify what you see)
4. STRUCTURE: Any visible confluence, stream widening, beaver dam, \
or pool? (describe briefly or 'none visible')
5. VERDICT: Is this worth investigating for fishing? \
(yes/maybe/no — one word)

Be direct and brief. If water is not visible, say so immediately."""

    try:
        response = llm.complete(
            "vision_screen",
            caller="vision_screening.screen_segment",
            user_id=user_id,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        vision_text = response.content[0].text

        verdict = "unverified"
        section5 = vision_text.lower().split("5.")[-1][:20]
        if "yes" in section5:
            verdict = "promising"
        elif "maybe" in section5:
            verdict = "possible"
        elif "no" in section5:
            verdict = "unlikely"

        access_blocked = "yes" in vision_text.lower().split("3.")[-1][:30]
        is_golf_course = "golf" in vision_text.lower()
        if is_golf_course:
            access_blocked = True

        structure_section = vision_text.split("4.")[-1][:100] if "4." in vision_text else ""

        is_engineered = any(
            word in vision_text.lower()
            for word in ["engineered", "ditch", "concrete", "channel"]
        )
        is_culvert = "culvert" in vision_text.lower()

        return {
            "screened": True,
            "verdict": verdict,
            "is_engineered_drainage": is_engineered,
            "is_culvert_crossing": is_culvert,
            "is_golf_course": is_golf_course,
            "access_blocked_by_structures": access_blocked,
            "vision_note": vision_text,
            "structure_detected": structure_section.strip(),
        }
    except Exception as e:
        return {
            "screened": False,
            "reason": str(e),
            "verdict": "unverified",
            "vision_note": None,
        }


def screen_candidates(
    candidates: list[dict],
    max_screens: int = 10,
    *,
    user_id: int | None,
) -> list[dict]:
    """Screen top candidates with vision.

    Adds vision_screening dict to each result. Filters out 'unlikely' verdicts.
    Caps API calls at max_screens — remaining candidates pass through unscreened.
    """
    screened = []
    api_calls = 0

    for candidate in candidates:
        if api_calls >= max_screens:
            candidate["vision_screening"] = {
                "screened": False,
                "reason": "vision budget exhausted",
                "verdict": "unverified",
            }
            screened.append(candidate)
            continue

        result = screen_segment(
            lat=candidate["centroid_lat"],
            lng=candidate["centroid_lng"],
            stream_order=candidate.get("stream_order", 0),
            watercourse_name=candidate.get("watercourse_name"),
            is_confluence=candidate.get("is_confluence_segment", False),
            user_id=user_id,
        )
        api_calls += 1
        candidate["vision_screening"] = result

        if result["verdict"] != "unlikely":
            screened.append(candidate)
        else:
            logger.info(
                "Vision filtered segment %s: %s",
                candidate.get("ogf_id"),
                (result.get("vision_note") or "")[:100],
            )

    return screened
