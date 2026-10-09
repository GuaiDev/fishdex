"""Tests for the Quebec Réseau-rivières water quality adapter — offline, tiny synthetic files."""

import io
import zipfile
from datetime import date, datetime

import httpx
import openpyxl

from src.ingest.jurisdictions.ca_qc import water_quality as wq

STATIONS_CSV = (
    "NO_BQMA;DESCRIPTION;ANNEE;LATITUDE;LONGITUDE;URL_ZGIEBV;URL_ZGIESL\n"
    "05040002;Saint-Maurice à Trois-Rivières;2020;46.35;-72.55;http://x/old.xlsx;\n"
    "05040002;Saint-Maurice à Trois-Rivières;2024;46.35;-72.55;http://x/new.xlsx;\n"
    "02340050;Chaudière;2024;46.73;-71.28;http://x/ch.xlsx;\n"
    "99999999;Sans coordonnées;2024;;;http://x/z.xlsx;\n"
)


def _chiffrier(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Données transposées"
    ws.append(
        ["N° LABO", "N° STATION", "DATE", "PH (pH)", "TEMP (°C)", "COND (µS/cm)", "TURB (UTN)"]
    )
    for r in rows:
        ws.append(r)
    path = tmp_path / "c.xlsx"
    wb.save(path)
    return path


def test_parse_stations_keeps_latest_year_and_skips_bad_rows():
    stations = {s["id"]: s for s in wq.parse_stations(STATIONS_CSV)}
    assert set(stations) == {"05040002", "02340050"}
    assert stations["05040002"]["chiffrier_url"] == "http://x/new.xlsx"
    assert stations["05040002"]["name"] == "Saint-Maurice à Trois-Rivières"


def test_stations_near_filters_by_radius():
    stations = wq.parse_stations(STATIONS_CSV)
    near = wq._stations_near(stations, 46.35, -72.55, 40)
    assert set(near) == {"05040002"}


def test_parse_chiffrier_maps_fields_and_filters(tmp_path):
    path = _chiffrier(
        tmp_path,
        [
            ["Q1", "05040002", datetime(2024, 6, 3), 7.4, 18.5, 120, 3.0],
            ["Q2", "05040002", datetime(2024, 7, 1), None, None, None, 2.0],  # nothing usable
            ["Q3", "02340050", datetime(2024, 6, 3), 7.0, 15.0, 90, 1.0],  # not wanted
            ["Q4", "05040002", datetime(2024, 8, 1), 17.6, 20.0, 100, 1.0],  # impossible pH
        ],
    )
    wanted = {"05040002": {"id": "05040002", "name": "SM", "lat": 46.35, "lng": -72.55}}
    out = wq.parse_chiffrier(path, wanted)
    assert len(out) == 1
    r = out[0]
    assert (r.record_id, r.jurisdiction, r.sampled_at) == ("qc_Q1", "CA-QC", date(2024, 6, 3))
    assert (r.ph, r.temp_c, r.conductivity_us_cm) == (7.4, 18.5, 120.0)
    assert r.do_mgl is None and r.turbidity_fnu is None


def test_missing_sheet_returns_empty(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Autre"
    path = tmp_path / "c.xlsx"
    wb.save(path)
    assert wq.parse_chiffrier(path, {"a": {}}) == []


def test_fetch_end_to_end_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_CACHE_DIR", tmp_path / "cache")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("stations_p.csv", "﻿" + STATIONS_CSV)
    chiffrier = _chiffrier(
        tmp_path, [["Q1", "05040002", datetime(2024, 6, 3), 7.4, 18.5, 120, None]]
    ).read_bytes()
    package = (
        b'{"result": {"resources": [{"name": "x", "format": "CSV", '
        b'"url": "http://x/IQBP_csv.zip"}]}}'
    )

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        if "package_show" in url:
            return Resp(package)
        return Resp(buf.getvalue() if url.endswith(".zip") else chiffrier)

    monkeypatch.setattr(httpx, "get", fake_get)
    out = wq.fetch_water_quality_readings(46.35, -72.55, 40)
    assert [r.record_id for r in out] == ["qc_Q1"]
