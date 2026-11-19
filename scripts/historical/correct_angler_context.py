"""One-time correction: fix wrong river names (Welland → Grand) in angler context."""

import sys

sys.path.insert(0, ".")

from src.storage.angler_context import correct_context
from src.storage.database import ensure_schema, get_db

db = get_db()
ensure_schema(db)
correct_context(db)
print("Done.")
