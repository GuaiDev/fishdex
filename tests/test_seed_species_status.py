"""P1.11: the shipped species file and COSEWIC export give a seed that screens."""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from src.cli.main import app
from src.services.context.species import has_status_data
from src.services.spot_card import build_spot_card
from src.storage.species_ranges import _row_to_range
from tests.test_spot_card import ECKARDT, eckardt, inat

REPO = Path(__file__).resolve().parent.parent


def _seed(db_factory, tmp_path, migrated_template_db, monkeypatch):
    path = tmp_path / "seed.db"
    shutil.copy(migrated_template_db, path)
    db = db_factory(path)
    monkeypatch.setattr("src.cli.main.get_db", lambda *a, **k: db)
    monkeypatch.setattr("src.services.species_ranges.get_db", lambda *a, **k: db)
    runner = CliRunner()
    for args in (
        ["load-species-ranges"],
        ["verify-species-status", "--file", str(REPO / "data/registry/cosewic_2024.csv")],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    return db


def test_seed_has_69_rows_12_verified_and_withholds_redside_dace(
    db_factory, tmp_path, migrated_template_db, monkeypatch
):
    db = _seed(db_factory, tmp_path, migrated_template_db, monkeypatch)

    assert db["species_ranges"].count == 69
    assert has_status_data(db, "CA-ON")
    # Paddlefish is extirpated and lists no jurisdictions, so count the whole table.
    rows = [_row_to_range(r) for r in db["species_ranges"].rows]
    assert sum(r.status_is_verified for r in rows) == 12

    inat(db, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    inat(db, "Semotilus atromaculatus", "Creek Chub", near=ECKARDT)
    card = build_spot_card(db, eckardt())

    assert [s.name for s in card.might_find.species] == ["Creek Chub"]
    assert card.meta.species_withheld_protected == 1
