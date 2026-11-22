"""Load private historical trip data kept outside the repository.

The data lives in the git-ignored `data/personal_trip_logs.json`: a JSON object
whose keys are the trip lists the scripts read: "historical_trips"
(batch_log_historical_trips.py), "historical_trips_v2"
(batch_log_historical_trips_v2.py) and "skipped_trips" (relog_skipped_trips.py).
Never commit this file.
"""

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
