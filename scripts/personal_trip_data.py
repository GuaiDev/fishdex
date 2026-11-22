"""Load private historical trip data kept outside the repository."""

import json
from pathlib import Path


DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "personal_trip_logs.json"


def load_personal_trip_data(key: str):
    """Return a private trip list, with a clear setup message when unavailable."""
    try:
        with DATA_FILE.open(encoding="utf-8") as data_file:
            data = json.load(data_file)
    except FileNotFoundError:
        raise SystemExit(
            f"Personal trip data is not available. Add your local file at {DATA_FILE} "
            "to use this script."
        ) from None

    try:
        return data[key]
    except KeyError:
        raise SystemExit(f"Personal trip data file is missing the '{key}' list.") from None
