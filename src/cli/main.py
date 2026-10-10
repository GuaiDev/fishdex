"""Entry point for the fishbot CLI."""

from datetime import date as date_type

import truststore
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.agent.chat import run_chat
from src.models.catch import Catch
from src.models.profile import Location, UserProfile
from src.models.trip import Trip
from src.storage.database import get_db
from src.storage.profile import load_profile, save_profile
from src.storage.trips import insert_trip, recent_trips

# Verify TLS against the OS trust store rather than certifi's bundle. A machine
# running TLS-inspecting software — a corporate proxy, or consumer antivirus
# such as Norton Web Shield — re-signs HTTPS with a private root that lives in
# the OS store and is not, and should not be, in certifi. Without this every
# network adapter fails with CERTIFICATE_VERIFY_FAILED while curl and git work
# fine, which reads as a dead API rather than a local trust problem.
#
# Verification is preserved; only the set of trusted roots changes. It patches
# SSLContext creation, so running it here — after the imports, before any
# client is constructed — is early enough.
truststore.inject_into_ssl()

app = typer.Typer(name="fishbot", help="Personal fishing exploration bot.")
console = Console()


@app.callback()
def _cli(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show INFO-level detail from adapters."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Errors only."),
) -> None:
    """Configure logging before any command runs.

    Nothing here ever called logging.basicConfig, so the root logger kept its
    default handler-less state and every logger.warning/error in the adapters
    went nowhere. Three separate silent failures this session traced back to
    it: PWQMN matching zero resources, the FMZ layer parsing every zone to
    None, and the diagnostics added to catch those. Warnings are visible by
    default now — a source that fails should say so without being asked.
    """
    import logging
    import os

    from rich.logging import RichHandler

    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.INFO
    else:
        # LOG_LEVEL has been in .env.example all along; it was simply never read.
        level = getattr(logging, os.environ.get("LOG_LEVEL", "WARNING").upper(), logging.WARNING)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
        force=True,  # replace anything a library configured on import
    )
    # httpx logs every request at INFO; too noisy even in verbose mode.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def run() -> None:
    """Start the fishing bot chat."""
    run_chat()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev only)"),
) -> None:
    """Start the FishBot API server."""
    import uvicorn

    uvicorn.run("src.api.main:app", host=host, port=port, reload=reload)


@app.command()
def log() -> None:
    """Interactively log a fishing trip."""
    profile = load_profile()
    today = date_type.today().isoformat()

    trip_date_str = typer.prompt("Date (YYYY-MM-DD)", default=today)
    location_name = typer.prompt("Location name (lake/river)")
    jurisdiction = typer.prompt(
        "Jurisdiction (ISO 3166-2, e.g. CA-ON)",
        default=profile.home_jurisdiction,
    )
    lat_raw = typer.prompt("Latitude (blank to skip)", default="", show_default=False)
    lng_raw = typer.prompt("Longitude (blank to skip)", default="", show_default=False)

    catches: list[Catch] = []
    if typer.confirm("Any catches?", default=False):
        while True:
            species = typer.prompt("  Species (blank to stop)", default="", show_default=False)
            if not species:
                break
            length_raw = typer.prompt("  Length cm (blank to skip)", default="", show_default=False)
            weight_raw = typer.prompt("  Weight kg (blank to skip)", default="", show_default=False)
            released = typer.confirm("  Released?", default=True)
            catches.append(
                Catch(
                    species=species,
                    length_cm=float(length_raw) if length_raw else None,
                    weight_kg=float(weight_raw) if weight_raw else None,
                    released=released,
                )
            )

    gear_used_raw = typer.prompt("Gear used (comma-separated)", default="", show_default=False)
    conditions_notes = typer.prompt("Conditions (free text)", default="", show_default=False)
    what_worked = typer.prompt("What worked", default="", show_default=False)
    what_didnt = typer.prompt("What didn't", default="", show_default=False)
    notes = typer.prompt("General notes", default="", show_default=False)

    trip = Trip(
        date=date_type.fromisoformat(trip_date_str),
        jurisdiction=jurisdiction,
        location_name=location_name,
        lat=float(lat_raw) if lat_raw else None,
        lng=float(lng_raw) if lng_raw else None,
        species_caught=catches,
        gear_used=[g.strip() for g in gear_used_raw.split(",") if g.strip()],
        conditions={"notes": conditions_notes} if conditions_notes else {},
        notes=notes,
        what_worked=what_worked,
        what_didnt=what_didnt,
    )

    db = get_db()
    trip_id = insert_trip(db, trip)
    console.print(f"[green]Saved trip #{trip_id}[/green] — {location_name} on {trip_date_str}")


@app.command()
def recent(
    limit: int = typer.Option(10, "--limit", "-n", help="How many trips to show"),
) -> None:
    """Show recent trips."""
    db = get_db()
    trips = recent_trips(db, limit=limit)
    if not trips:
        console.print("[dim]No trips logged yet. Run `fishbot log` to record one.[/dim]")
        return

    table = Table(title=f"Recent trips ({len(trips)})")
    table.add_column("Date")
    table.add_column("Location")
    table.add_column("Jurisdiction")
    table.add_column("Caught")
    table.add_column("Notes", overflow="fold")
    for t in trips:
        species = ", ".join(c.species for c in t.species_caught) if t.species_caught else "skunked"
        notes_bits = [t.what_worked, t.what_didnt, t.notes]
        notes_combined = " | ".join(s for s in notes_bits if s)
        table.add_row(
            t.date.isoformat(),
            t.location_name,
            t.jurisdiction,
            species,
            notes_combined,
        )
    console.print(table)


@app.command()
def profile() -> None:
    """View and optionally edit your fishing profile."""
    p = load_profile()
    _print_profile(p)

    if not typer.confirm("Edit profile?", default=False):
        return

    home_jurisdiction = typer.prompt("Home jurisdiction (ISO 3166-2)", default=p.home_jurisdiction)
    home_name = typer.prompt(
        "Home location name",
        default=p.home_location.name if p.home_location else "",
    )
    home_lat_raw = typer.prompt(
        "Home latitude",
        default=str(p.home_location.lat) if p.home_location else "",
    )
    home_lng_raw = typer.prompt(
        "Home longitude",
        default=str(p.home_location.lng) if p.home_location else "",
    )
    target_species_raw = typer.prompt(
        "Target species (comma-separated)",
        default=", ".join(p.target_species),
    )
    fishing_style = typer.prompt("Fishing style", default=p.fishing_style)
    skill_level = typer.prompt("Skill level", default=p.skill_level)
    preferences = typer.prompt("Preferences (free text)", default=p.preferences)

    home_location = None
    if home_name and home_lat_raw and home_lng_raw:
        home_location = Location(
            name=home_name,
            lat=float(home_lat_raw),
            lng=float(home_lng_raw),
        )

    updated = UserProfile(
        home_jurisdiction=home_jurisdiction,
        frequented_jurisdictions=p.frequented_jurisdictions,
        home_location=home_location,
        target_species=[s.strip() for s in target_species_raw.split(",") if s.strip()],
        gear=p.gear,
        budget=p.budget,
        skill_level=skill_level,
        fishing_style=fishing_style,
        preferences=preferences,
    )
    save_profile(updated)
    console.print("[green]Profile saved.[/green]")


# ── ingest source isolation ───────────────────────────────────────────────────


class _SourceResult:
    """Outcome of one ingest source. Failure is data, not an exception."""

    __slots__ = ("name", "ok", "detail", "error")

    def __init__(self, name: str, ok: bool, detail: str = "", error: str = "") -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.error = error


def _run_source(results: list, name: str, announce: str, fn, *args, **kwargs) -> object:
    """Run one ingest source in isolation.

    A single flaky source used to abort the whole run: a GBIF timeout took out
    thirteen unrelated sources, including the FMZ boundary layer the run was
    started for. Each source now fails on its own and the rest continue.

    Returns whatever the source returned, or None if it failed — callers must
    tolerate None rather than assume success.
    """
    import logging

    console.print(f"[dim]{announce}[/dim]")
    try:
        value = fn(*args, **kwargs)
    except KeyboardInterrupt:
        raise  # never swallow the user's Ctrl-C
    except Exception as exc:  # noqa: BLE001 - isolation is the whole point
        logging.getLogger(__name__).error(
            "%s failed: %s: %s", name, type(exc).__name__, exc, exc_info=True
        )
        console.print(f"[red]  {name} FAILED — {type(exc).__name__}: {exc}[/red]")
        console.print("[dim]  continuing with the remaining sources…[/dim]")
        results.append(_SourceResult(name, False, error=f"{type(exc).__name__}: {exc}"))
        return None

    results.append(_SourceResult(name, True, detail=_describe_result(value)))
    return value


def _describe_result(value: object) -> str:
    """Short human summary of whatever a source returned."""
    if value is None:
        return "done"
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _print_ingest_summary(results: list) -> None:
    """Show what landed and what did not. Never hide a failure in a wall of green."""
    table = Table(title="Ingest summary")
    table.add_column("Source", overflow="fold")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for r in results:
        table.add_row(
            r.name,
            "[green]ok[/green]" if r.ok else "[red]FAILED[/red]",
            r.detail if r.ok else r.error,
        )
    console.print(table)

    failed = [r for r in results if not r.ok]
    if failed:
        console.print(
            f"[red]{len(failed)} of {len(results)} sources failed: "
            f"{', '.join(r.name for r in failed)}[/red]"
        )
        console.print(
            "[dim]Re-run to retry them; sources that succeeded are cached and will "
            "skip quickly.[/dim]"
        )
    else:
        console.print(f"[green]All {len(results)} sources succeeded.[/green]")


@app.command()
def ingest(
    radius_km: float = typer.Option(300.0, "--radius", help="Search radius in km"),
    days_back: int = typer.Option(
        90, "--days", help="How many days of iNaturalist history to pull"
    ),  # noqa: E501
    lat: float = typer.Option(None, "--lat", help="Override center latitude"),
    lng: float = typer.Option(None, "--lng", help="Override center longitude"),
) -> None:
    """Pull fish observations from iNaturalist and GBIF near your home location."""
    from src.services.gbif import fetch_and_store as gbif_fetch_and_store
    from src.services.observations import fetch_and_store as inat_fetch_and_store
    from src.services.osm import fetch_and_store as osm_fetch_and_store
    from src.services.stream_gauge import fetch_and_store as wsc_fetch_and_store

    profile = load_profile()

    if lat is not None and lng is not None:
        center_lat, center_lng = lat, lng
        center_name = f"({lat}, {lng})"
    elif profile.home_location:
        center_lat, center_lng = profile.home_location.lat, profile.home_location.lng
        center_name = profile.home_location.name
    else:
        console.print(
            "[red]Home location not set. Run `fishbot profile` or pass --lat/--lng.[/red]"
        )
        raise typer.Exit(1)

    import importlib as _importlib

    from src.services.benthic import ingest_benthic_data
    from src.services.ebird import fetch_and_store as ebird_fetch_and_store
    from src.services.geology import ingest_geology_data
    from src.services.hydrology import ingest_hydro_network
    from src.services.insights import seed_dispersal_insights
    from src.services.reddit import fetch_and_store as reddit_fetch_and_store
    from src.services.regulations import ingest_fmz_boundaries, ingest_regulations
    from src.services.species_ranges import load_and_store as species_load_and_store
    from src.services.stocking import ingest_stocking_data
    from src.services.water_quality import ingest_water_quality_data
    from src.storage.stream_temperature import is_data_loaded as _temp_loaded

    _parks = _importlib.import_module("src.ingest.global.provincial_parks")
    _cas = _importlib.import_module("src.ingest.global.ca_boundaries")
    _crown = _importlib.import_module("src.ingest.global.crown_land")

    results: list = []
    r = results  # every source below is isolated; one failure does not stop the rest

    _run_source(
        r,
        "iNaturalist",
        f"Fetching iNaturalist observations within {radius_km}km of {center_name}, "
        f"last {days_back} days…",
        inat_fetch_and_store,
        center_lat,
        center_lng,
        radius_km=radius_km,
        days_back=days_back,
    )
    _run_source(
        r,
        "GBIF",
        f"Fetching GBIF institutional records within {radius_km}km of {center_name}…",
        gbif_fetch_and_store,
        center_lat,
        center_lng,
        radius_km=radius_km,
    )
    _run_source(
        r,
        "WSC gauges",
        f"Fetching WSC stream gauge readings within {radius_km:.0f}km of {center_name}…",
        wsc_fetch_and_store,
        center_lat,
        center_lng,
        radius_km=radius_km,
    )
    _run_source(
        r,
        "OpenStreetMap",
        f"Fetching OSM water features (50km) and access points (25km) near {center_name}…",
        osm_fetch_and_store,
        center_lat,
        center_lng,
    )
    _run_source(
        r,
        "MNRF stocking",
        "Downloading MNRF fish stocking records (30-day cache)…",
        ingest_stocking_data,
    )
    _run_source(
        r,
        "Species ranges",
        "Loading Ontario species range database…",
        species_load_and_store,
    )
    _run_source(
        r,
        "Reddit",
        "Fetching Reddit fishing community posts…",
        reddit_fetch_and_store,
    )
    _run_source(
        r,
        "Ontario Hydro Network",
        f"Fetching OHN stream segments and barriers ({radius_km:.0f}km bbox)…",
        ingest_hydro_network,
        center_lat,
        center_lng,
        radius_km,
    )
    _run_source(
        r,
        "Regulations",
        "Downloading and parsing the MNRF Fishing Regulations Summary…",
        ingest_regulations,
    )
    _run_source(
        r,
        "FMZ boundaries",
        "Downloading Ontario FMZ boundary polygons…",
        ingest_fmz_boundaries,
    )
    _run_source(
        r,
        "PWQMN water quality",
        "Downloading PWQMN water quality field data (2021–present)…",
        ingest_water_quality_data,
    )
    _run_source(
        r,
        "CABIN benthic",
        "Downloading CABIN benthic macroinvertebrate data…",
        ingest_benthic_data,
    )
    _run_source(
        r,
        "Geology",
        f"Fetching Ontario surficial geology within {radius_km:.0f}km of {center_name}…",
        ingest_geology_data,
        center_lat,
        center_lng,
        radius_km,
    )
    _run_source(
        r,
        "eBird",
        f"Fetching eBird piscivore observations within {radius_km:.0f}km of {center_name}…",
        ebird_fetch_and_store,
        center_lat,
        center_lng,
        radius_km,
    )
    _run_source(
        r,
        "Dispersal insights",
        "Seeding waterfowl dispersal behavioral insights…",
        seed_dispersal_insights,
    )
    _run_source(
        r,
        "Provincial parks",
        f"Fetching Ontario Provincial Parks within 200km of {center_name}…",
        _parks.fetch_and_store,
        get_db(),
        center_lat,
        center_lng,
        radius_km=200.0,
    )
    _run_source(
        r,
        "CA boundaries",
        f"Fetching Conservation Authority boundaries within 200km of {center_name}…",
        _cas.fetch_and_store,
        get_db(),
        center_lat,
        center_lng,
        radius_km=200.0,
    )
    _run_source(
        r,
        "Crown land",
        f"Fetching Ontario Crown Land boundaries within 100km of {center_name}…",
        _crown.fetch_and_store,
        get_db(),
        center_lat,
        center_lng,
        radius_km=100.0,
    )

    if not _temp_loaded(get_db()):
        console.print(
            "[dim]Stream temperature: not loaded — run `make ingest-hydat` once to enable[/dim]"
        )

    _print_ingest_summary(results)

    # Check if SDM retraining is warranted based on new trip log data
    _check_sdm_retrain_needed(get_db())


@app.command(name="weekly-ingest")
def weekly_ingest(
    areas_file: str = typer.Option(
        "data/ingest_areas.json", "--areas", help="JSON file listing the areas to ingest"
    ),
    only: str = typer.Option(
        None, "--only", help="Run only areas whose label contains this text (case-insensitive)"
    ),
    days_back: int = typer.Option(
        90, "--days", help="iNaturalist history to pull per area (0 = all history)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would run, fetch nothing"),
) -> None:
    """Ingest every area in data/ingest_areas.json, one after another.

    Replaces the old weekly GitHub Action that POSTed each area to a hosted
    server. Runs the same service code as the /ingest/data* endpoints, against
    the local database. Exits non-zero if any dataset failed, so a cron job's
    mail or log shows it.
    """
    from pathlib import Path

    from src.services.area_ingest import load_areas, run_area

    areas = load_areas(Path(areas_file))
    if only:
        areas = [a for a in areas if only.lower() in a.label.lower()]
        if not areas:
            console.print(f"[red]No area label contains {only!r}.[/red]")
            raise typer.Exit(1)

    jobs = [(a, s) for a in areas for s in a.sources]
    if dry_run:
        table = Table(title=f"weekly-ingest plan ({len(jobs)} runs)")
        for col in ("Area", "Jurisdiction", "Source", "lat", "lng", "radius km"):
            table.add_column(col)
        for a, s in jobs:
            table.add_row(
                a.label, a.jurisdiction, s.value, str(a.lat), str(a.lng), str(a.radius_for(s))
            )
        console.print(table)
        return

    results = []
    for i, (a, s) in enumerate(jobs, 1):
        console.print(f"[dim]({i}/{len(jobs)}) {a.label} — {s.value}…[/dim]")
        results.append(run_area(a, s, days_back=days_back or None))

    table = Table(title="Weekly ingest summary")
    table.add_column("Area", overflow="fold")
    table.add_column("Source")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for r in results:
        stored = ", ".join(f"{k}: {v}" for k, v in r.stored.items())
        failed = ", ".join(f"{k}: {v}" for k, v in r.failed.items())
        table.add_row(
            r.label,
            r.source.value,
            "[green]ok[/green]" if r.ok else "[red]FAILED[/red]",
            "; ".join(x for x in (stored, failed) if x),
        )
    console.print(table)

    failed_runs = [r for r in results if not r.ok]
    if failed_runs:
        console.print(f"[red]{len(failed_runs)} of {len(results)} runs had a failed dataset.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]All {len(results)} runs succeeded.[/green]")


@app.command(name="ingest-hydat")
def ingest_hydat() -> None:
    """Derive stream thermal regime from PWQMN water quality readings in the database.

    Run after make ingest.
    """
    import importlib

    _hydat = importlib.import_module("src.ingest.global.hydat_temperature")
    count = _hydat.derive_from_pwqmn(get_db())
    console.print(f"Derived thermal regime for {count} stations from PWQMN water quality data")


@app.command(name="build-features")
def build_features() -> None:
    """Build the SDM feature matrix from all Phase 1 data layers."""
    import time

    from src.services.sdm_features import build_feature_matrix, coverage_fraction

    t0 = time.time()
    df = build_feature_matrix(get_db())
    elapsed = time.time() - t0
    pct = coverage_fraction(df) * 100
    console.print(
        f"Feature matrix: {len(df):,} segments, 16 features, {pct:.1f}% coverage ({elapsed:.1f}s)"
    )


@app.command(name="train-sdm")
def train_sdm() -> None:
    """Train Random Forest SDMs for 9 species. Research tool, off the request path.

    Loads the feature matrix from data/processed/sdm_feature_matrix.parquet,
    trains one calibrated RF model per species, reports spatial-CV AUC, and
    saves model joblibs to data/processed/sdm_models/.

    It no longer writes predictions to the database. The sdm_predictions table
    had exactly one reader — an exploration path that rendered its output as
    "Recorded nearby", which is a prediction wearing an observation's clothes —
    and that path is gone. What was left was a table accumulating rows nobody
    read, which is a trap: the next person to find it populated would
    reasonably assume it meant something.

    Reinstating a consumer is a deliberate decision to make then, with the
    AUC in front of you. It is one call to upsert_predictions when it happens.

    Expected runtime: 5–15 minutes.
    """
    import time
    from pathlib import Path as _Path

    import pandas as pd
    from rich.table import Table

    from src.services.sdm_training import (
        SPECIES_TO_TRAIN,
        save_model,
        train_species_model,
    )

    parquet = _Path("data/processed/sdm_feature_matrix.parquet")
    if not parquet.exists():
        console.print("[red]Feature matrix not found. Run `make build-features` first.[/red]")
        raise typer.Exit(1)

    db = get_db()
    console.print("[dim]Loading feature matrix…[/dim]")
    feature_matrix = pd.read_parquet("data/processed/sdm_feature_matrix.parquet")
    n_feat = len(feature_matrix.columns) - 3
    console.print(f"[dim]  {len(feature_matrix):,} segments, {n_feat} features[/dim]")

    results_table = Table(title="SDM Training Results")
    results_table.add_column("Species")
    results_table.add_column("n_presence", justify="right")
    results_table.add_column("n_absence", justify="right")
    results_table.add_column("CV AUC", justify="right")
    results_table.add_column("Status")

    total_t0 = time.time()
    trained = 0

    for species in SPECIES_TO_TRAIN:
        console.print(f"[dim]Training {species}…[/dim]")
        t0 = time.time()
        try:
            result = train_species_model(species, db, feature_matrix)
            save_model(result)

            elapsed = time.time() - t0
            # None means spatial CV had no usable fold — the model is
            # unvalidated, which must not render as a number.
            _auc = result.get("spatial_cv_auc")
            auc_str = f"{_auc:.3f}" if _auc is not None else "[yellow]unvalidated[/yellow]"
            results_table.add_row(
                species,
                str(result["n_presence"]),
                str(result["n_pseudo_absence"]),
                auc_str,
                f"[green]OK[/green] ({elapsed:.0f}s)",
            )
            trained += 1
        except ValueError as exc:
            elapsed = time.time() - t0
            results_table.add_row(species, "—", "—", "—", f"[yellow]Skipped: {exc}[/yellow]")
        except Exception as exc:
            elapsed = time.time() - t0
            results_table.add_row(species, "—", "—", "—", f"[red]Error: {exc}[/red]")

    total_elapsed = time.time() - total_t0
    console.print(results_table)
    console.print(
        f"[green]Trained {trained}/{len(SPECIES_TO_TRAIN)} models "
        f"in {total_elapsed:.0f}s — predictions stored in DB[/green]"
    )


@app.command(name="sdm-contributions")
def sdm_contributions() -> None:
    """Show how many trip log catches contribute to each SDM model."""
    import os

    import joblib

    model_dir = "data/processed/sdm_models"
    if not os.path.isdir(model_dir):
        console.print("[red]No models found. Run `make train-sdm` first.[/red]")
        raise typer.Exit(1)

    bundles = []
    for f in sorted(os.listdir(model_dir)):
        if not f.endswith(".joblib"):
            continue
        b = joblib.load(os.path.join(model_dir, f))
        bundles.append(b)

    if not bundles:
        console.print("[red]No .joblib model files found in data/processed/sdm_models.[/red]")
        raise typer.Exit(1)

    from rich.table import Table

    tbl = Table(title="SDM Training Data — Trip Log Contributions")
    tbl.add_column("Species", min_width=30)
    tbl.add_column("iNat", justify="right")
    tbl.add_column("GBIF", justify="right")
    tbl.add_column("Trip Log", justify="right")
    tbl.add_column("Total Pres.", justify="right")
    tbl.add_column("CV AUC", justify="right")

    for b in bundles:
        species = b.get("species", "?")
        n_inat = b.get("n_inat", "?")
        n_gbif = b.get("n_gbif", "?")
        n_trip = b.get("n_trip_log", 0)
        n_pres = b.get("n_presence", "?")
        auc = b.get("spatial_cv_auc", None)
        auc_str = f"{auc:.3f}" if auc is not None else "?"
        trip_str = f"[green]{n_trip}[/green]" if n_trip > 0 else str(n_trip)
        tbl.add_row(species, str(n_inat), str(n_gbif), trip_str, str(n_pres), auc_str)

    console.print(tbl)
    console.print("\n[dim]Run 'fishbot train-sdm' to retrain with current trip log data.[/dim]")


@app.command(name="compute-access")
def compute_access() -> None:
    """Compute access scores for all OHN stream segments.

    Requires the feature matrix (make build-features) and provincial parks ingest.
    Scores are cached to data/processed/access_scores.parquet.
    """
    import time
    from pathlib import Path as _Path

    import numpy as np
    import pandas as pd

    from src.services.accessibility import compute_access_scores

    parquet = _Path("data/processed/sdm_feature_matrix.parquet")
    if not parquet.exists():
        console.print("[red]Feature matrix not found. Run `make build-features` first.[/red]")
        raise typer.Exit(1)

    db = get_db()
    console.print("[dim]Loading feature matrix…[/dim]")
    feature_matrix = pd.read_parquet(parquet)

    t0 = time.time()
    console.print(f"[dim]Scoring {len(feature_matrix):,} segments…[/dim]")
    scores = compute_access_scores(db, feature_matrix)
    elapsed = time.time() - t0

    q = np.quantile(scores.values, [0.25, 0.5, 0.75])
    park_count = 0
    if "provincial_parks" in db.table_names():
        park_count = db.execute("SELECT COUNT(*) FROM provincial_parks").fetchone()[0]

    console.print(
        f"[green]Access scores computed for {len(scores):,} segments in {elapsed:.1f}s[/green]"
    )
    console.print(f"Score distribution — Q1: {q[0]:.3f} | median: {q[1]:.3f} | Q3: {q[2]:.3f}")
    console.print(
        f"Parks loaded: {park_count} | Scores cached to data/processed/access_scores.parquet"  # noqa: E501
    )


@app.command(name="ingest-fmz")
def ingest_fmz() -> None:
    """Download just the Ontario FMZ boundary polygons.

    Separate from `ingest` so one flaky source does not force a full re-run:
    zone resolution fails closed without this layer, so it is worth being able
    to fetch on its own.
    """
    from src.services.regulations import ingest_fmz_boundaries

    console.print("[dim]Downloading Ontario FMZ boundary polygons…[/dim]")
    n = ingest_fmz_boundaries()
    if n:
        console.print(f"[green]FMZ boundaries stored: {n} zones[/green]")
    else:
        console.print("[red]No FMZ boundaries fetched — zone resolution stays closed.[/red]")


_REGISTRY_DIR = "data/registry"
_DEFAULT_REGISTRY = f"{_REGISTRY_DIR}/cosewic_2024.csv"


@app.command(name="verify-species-status")
def verify_species_status(
    file: str = typer.Option(
        _DEFAULT_REGISTRY,
        "--file",
        help=f"Registry export, CSV or JSON. Exports live in {_REGISTRY_DIR}/.",
    ),
    source: str = typer.Option(
        "",
        "--source",
        help="Citation. Defaults to the export's own 'source' column.",
    ),
    url: str = typer.Option(
        "",
        "--url",
        help="Public URL. Defaults to the export's own 'source_url' column.",
    ),
) -> None:
    """Apply conservation statuses from a downloaded COSEWIC/SARA registry export.

    Species not present in the export stay unverified and keep failing closed.

    The citation is read off the file unless overridden. The exports written
    for this command name their own source and URL on every row, and retyping
    them is how a run stamps the wrong citation onto a status.
    """
    from pathlib import Path as _P

    from src.services.status_verification import (
        apply_verified_statuses,
        load_registry_file,
        registry_citation,
    )

    path = _P(file)
    if not path.exists():
        console.print(f"[red]No such file: {path}[/red]")
        if file == _DEFAULT_REGISTRY:
            console.print(f"[dim]Put a registry export in {_REGISTRY_DIR}/, or pass --file.[/dim]")
        raise typer.Exit(1)

    db = get_db()
    registry = load_registry_file(path)

    file_source, file_url = registry_citation(registry)
    source = source or file_source or ""
    url = url or file_url or ""
    if not source or not url:
        # apply_verified_statuses refuses without both, but failing here says
        # which half is missing and where it was looked for.
        console.print(
            "[red]No citation. The export carries no consistent source/source_url "
            "column, so pass --source and --url explicitly.[/red]"
        )
        raise typer.Exit(1)
    console.print(f"[dim]Citation:               {source}[/dim]")

    summary = apply_verified_statuses(db, registry, source=source, source_url=url)

    # Every count prints every run. "Verified: 0" is a legitimate outcome here,
    # so without the skip counts beside it there is nothing to distinguish a
    # narrow export from a join that matched nothing — which is exactly how a
    # name-column mismatch went unnoticed until someone read the source.
    console.print(f"[dim]Registry entries read:  {summary['registry_entries']:,}[/dim]")
    console.print(f"[green]Verified:               {summary['verified']:,}[/green]")
    console.print(f"[yellow]Left unverified:        {summary['left_unverified']:,}[/yellow]")
    console.print(f"[dim]Not in registry:        {summary['skipped_not_in_registry']:,}[/dim]")
    console.print(f"[dim]Matched, no status:     {summary['matched_no_usable_status']:,}[/dim]")

    unmatched = summary["unmatched_registry_entries"]
    if unmatched:
        console.print(
            f"[yellow]Registry entries that matched no species ({len(unmatched)}):[/yellow]"
        )
        for name in unmatched[:15]:
            console.print(f"   {name}")

    if summary["registry_entries"] and summary["verified"] == 0:
        console.print(
            "[red]Nothing matched. The registry and species_ranges do not share "
            "a name column — check the header of your export.[/red]"
        )

    cleared = summary["cleared_generated_statuses"]
    if cleared:
        console.print(
            f"[dim]Cleared generated statuses the registry did not cover "
            f"({len(cleared)}) — they would otherwise sit under the new "
            f"citation:[/dim]"
        )
        for c in cleared[:10]:
            console.print(f"   {c}")

    if summary["rejected_values"]:
        console.print(f"[red]Rejected values ({len(summary['rejected_values'])}):[/red]")
        for r in summary["rejected_values"][:10]:
            console.print(f"   {r}")
    console.print(summary["note"])


@app.command(name="compute-untapped")
def compute_untapped() -> None:
    """Compute untapped potential: (1 - pressure) × access × structure × remoteness.

    Gated by plausibility — segments with affirmative evidence of not being
    fishable water score 0. There is no habitat term and no species filter:
    the ranking measures how unreported a stretch is, not whether fish are in it.

    Requires access scores (make compute-access).
    Shows top 5 results sorted by untapped_score.
    """
    import time
    from pathlib import Path as _Path

    import pandas as pd
    from rich.table import Table

    from src.services.untapped_potential import compute_untapped_potential

    parquet = _Path("data/processed/sdm_feature_matrix.parquet")
    if not parquet.exists():
        console.print("[red]Feature matrix not found. Run `make build-features` first.[/red]")
        raise typer.Exit(1)

    db = get_db()
    console.print("[dim]Loading feature matrix…[/dim]")
    feature_matrix = pd.read_parquet(parquet)

    t0 = time.time()
    console.print("[dim]Computing untapped potential…[/dim]")
    try:
        df = compute_untapped_potential(db, feature_matrix)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    elapsed = time.time() - t0

    console.print(
        f"[green]Untapped potential computed for {len(df):,} segments in {elapsed:.1f}s[/green]"
    )

    top = df[df["untapped_score"] > 0].head(5)
    if top.empty:
        console.print("[yellow]No segments with non-zero untapped score found.[/yellow]")
        return

    table = Table(title="Top 5 Untapped Water")
    table.add_column("Name", overflow="fold")
    table.add_column("Order", justify="right")
    table.add_column("Access", justify="right")
    table.add_column("Pressure", justify="right")
    table.add_column("Untapped", justify="right")
    table.add_column("Lat/Lng")

    for _, row in top.iterrows():
        name = str(row["watercourse_name"]) if row["watercourse_name"] else "(unnamed)"
        table.add_row(
            name,
            str(int(row["stream_order"])) if not pd.isna(row["stream_order"]) else "—",
            f"{row['access_score']:.3f}",
            f"{row['observation_pressure']:.3f}",
            f"{row['untapped_score']:.4f}",
            f"{row['centroid_lat']:.4f}, {row['centroid_lng']:.4f}",
        )

    console.print(table)


@app.command()
def context(clear: bool = typer.Option(False, "--clear", help="Clear the angler context")) -> None:
    """View or clear the persistent angler context document."""
    from src.storage.angler_context import load_context, save_context

    db = get_db()

    if clear:
        save_context(db, "")
        console.print("[green]Angler context cleared.[/green]")
        return

    ctx = load_context(db)
    if not ctx:
        console.print(
            "[dim]No angler context yet. Start chatting and it will build up automatically.[/dim]"
        )
        return

    console.print("\n=== FishBot knows this about you ===\n")
    console.print(ctx)
    console.print("\n====================================")
    console.print("[dim]Run 'fishbot context --clear' to reset.[/dim]")


@app.command()
def history(
    sessions: int = typer.Option(5, "--sessions", "-n", help="Number of sessions to show"),
) -> None:
    """Show recent chat sessions."""
    db = get_db()
    rows = list(
        db.execute(
            """
        SELECT session_id, started_at, turn_count, summary
        FROM chat_sessions
        WHERE turn_count > 1
        ORDER BY started_at DESC
        LIMIT ?
    """,
            [sessions],
        ).fetchall()
    )

    if not rows:
        console.print("[dim]No chat sessions yet.[/dim]")
        return

    for session_id, started_at, turn_count, summary in rows:
        console.print(f"\n{'=' * 60}")
        console.print(f"Session: {started_at[:16]}  ({turn_count} turns)")
        if summary:
            console.print(f"\n{summary}")
        else:
            console.print("[dim](no summary yet)[/dim]")


@app.command()
def usage(days: int = typer.Option(7, "--days", "-d", help="Number of days to show")) -> None:
    """Show API usage summary for the last N days."""
    db = get_db()
    rows = list(
        db.execute(f"""
        SELECT
            DATE(timestamp) as day,
            SUM(input_tokens) as input_tokens,
            SUM(output_tokens) as output_tokens,
            SUM(total_tokens) as total_tokens,
            COUNT(*) as api_calls,
            SUM(tool_calls_made) as tool_calls,
            ROUND(AVG(tool_calls_made), 1) as avg_tools_per_turn
        FROM api_usage
        WHERE timestamp >= DATE('now', '-{days} days')
        GROUP BY DATE(timestamp)
        ORDER BY day DESC
    """).fetchall()
    )

    if not rows:
        console.print("No usage data yet.")
        return

    console.print(f"\nAPI Usage — last {days} days\n")
    console.print(
        f"{'Day':<12} {'Input':>8} {'Output':>8} {'Total':>8} "
        f"{'Calls':>6} {'Tools':>6} {'Avg Tools':>10}"
    )
    console.print("-" * 65)
    for r in rows:
        console.print(f"{r[0]:<12} {r[1]:>8,} {r[2]:>8,} {r[3]:>8,} {r[4]:>6} {r[5]:>6} {r[6]:>10}")

    totals = db.execute(f"""
        SELECT SUM(input_tokens), SUM(output_tokens), SUM(total_tokens), COUNT(*)
        FROM api_usage
        WHERE timestamp >= DATE('now', '-{days} days')
    """).fetchone()
    console.print("-" * 65)
    console.print(f"{'TOTAL':<12} {totals[0]:>8,} {totals[1]:>8,} {totals[2]:>8,} {totals[3]:>6}")

    # $3 per 1M input tokens, $15 per 1M output tokens (Claude Sonnet pricing)
    est_cost = (totals[0] / 1_000_000 * 3) + (totals[1] / 1_000_000 * 15)
    console.print(f"\nEstimated cost: ${est_cost:.4f} USD")
    console.print(
        "(Based on Claude Sonnet pricing — verify current rates at console.anthropic.com)"
    )


@app.command(name="cache-status")
def cache_status() -> None:
    """Show synthesis cache contents and hit rates."""
    db = get_db()
    try:
        rows = list(
            db.execute("""
            SELECT location_name, cache_key, hit_count, computed_at,
                   LENGTH(synthesis) as synthesis_chars
            FROM segment_synthesis
            ORDER BY hit_count DESC, computed_at DESC
        """).fetchall()
        )
    except Exception:
        console.print("No synthesis cache entries yet.")
        return

    if not rows:
        console.print("Synthesis cache is empty.")
        return

    from rich.table import Table

    tbl = Table(title=f"Synthesis Cache — {len(rows)} entries")
    tbl.add_column("Location", min_width=35)
    tbl.add_column("Hits", justify="right")
    tbl.add_column("Size", justify="right")
    tbl.add_column("Computed")

    for row in rows:
        name = str(row[0] or row[1] or "unknown")[:34]
        hits = str(row[2] or 0)
        chars = str(row[4] or 0)
        computed = str(row[3] or "")[:19]
        tbl.add_row(name, hits, chars, computed)

    console.print(tbl)
    total_hits = sum(r[2] or 0 for r in rows)
    console.print(f"\nTotal cache hits: {total_hits}")
    console.print("[dim]Run 'fishbot cache-clear' to reset the cache.[/dim]")


@app.command(name="cache-clear")
def cache_clear() -> None:
    """Clear the synthesis cache (forces fresh analysis on next query)."""
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) FROM segment_synthesis").fetchone()[0]
        db.execute("DELETE FROM segment_synthesis")
        db.conn.commit()
        console.print(f"Cleared {count} cache entries.")
    except Exception as e:
        console.print(f"[red]Error clearing cache: {e}[/red]")


@app.command(name="tool-stats")
def tool_stats(days: int = typer.Option(7, "--days", "-d", help="Number of days to show")) -> None:
    """Show which tools are being called most over the last N days."""
    db = get_db()
    rows = list(
        db.execute(f"""
        SELECT tool_name,
               COUNT(*) as calls,
               COUNT(DISTINCT session_id) as sessions
        FROM tool_usage
        WHERE timestamp >= datetime('now', '-{days} days')
        GROUP BY tool_name
        ORDER BY calls DESC
    """).fetchall()
    )

    if not rows:
        console.print(f"No tool usage data in the last {days} days.")
        return

    from rich.table import Table

    tbl = Table(title=f"Tool Usage — last {days} days")
    tbl.add_column("Tool", min_width=45)
    tbl.add_column("Calls", justify="right")
    tbl.add_column("Sessions", justify="right")

    for r in rows:
        tbl.add_row(r[0], str(r[1]), str(r[2]))

    console.print(tbl)


@app.command()
def invite(note: str = typer.Option("", "--note", "-n", help="Note for this invite code")) -> None:
    """Generate an invite code for a friend."""
    from src.auth.auth import generate_invite_code

    db = get_db()
    code = generate_invite_code(db, created_by=1, note=note)
    console.print(f"\n[green]Invite code: [bold]{code}[/bold][/green]")
    console.print("Share this URL: http://localhost:8000/app (wherever `make serve` runs)")
    console.print(f"They enter: [bold]{code}[/bold] + choose a username")
    if note:
        console.print(f"Note: {note}")


@app.command()
def users() -> None:
    """List all registered users and their usage."""
    from rich.table import Table

    db = get_db()
    try:
        rows = list(
            db.execute("""
            SELECT u.id, u.username, u.display_name, u.role,
                   u.created_at,
                   COALESCE(SUM(du.message_count), 0) as total_messages
            FROM users u
            LEFT JOIN daily_usage du ON du.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id
        """).fetchall()
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    if not rows:
        console.print("[dim]No users yet.[/dim]")
        return

    tbl = Table(title="Registered Users")
    tbl.add_column("ID", justify="right")
    tbl.add_column("Username")
    tbl.add_column("Display Name")
    tbl.add_column("Role")
    tbl.add_column("Joined")
    tbl.add_column("Total Msgs", justify="right")

    for r in rows:
        tbl.add_row(str(r[0]), r[1], r[2] or "", r[3] or "", (r[4] or "")[:10], str(r[5]))

    console.print(tbl)


@app.command()
def token() -> None:
    """Get or refresh the admin Bearer token for the web API."""
    import secrets
    import sys
    from datetime import datetime, timedelta

    sys.path.insert(0, "src")
    from src.storage.database import ensure_schema
    from src.storage.database import get_db as _get_db

    db = _get_db()
    ensure_schema(db)

    try:
        user = next(db["users"].rows_where("id = 1"), None)
        if not user:
            console.print("No admin user found.")
            return

        new_token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(days=90)).isoformat()
        db["user_sessions"].insert(
            {
                "user_id": 1,
                "token": new_token,
                "expires_at": expires,
                "last_used_at": datetime.now().isoformat(),
            }
        )
        db.conn.commit()

        console.print("\n[green]Admin token (valid 90 days):[/green]")
        console.print(f"Bearer {new_token}")
        console.print("\n[dim]Save this. Use it to generate invite codes:[/dim]")
        console.print("curl -X POST http://localhost:8000/admin/invite \\")
        console.print(f'  -H "Authorization: Bearer {new_token}" \\')
        console.print('  -d \'{"note": "friendsname"}\'')
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def _check_sdm_retrain_needed(db) -> None:
    """
    Check if any species has enough new trip log presence records to
    warrant retraining. Threshold: new trip log points > 20% of existing
    iNat+GBIF count for that species.
    Prints a recommendation but does NOT auto-retrain.
    """
    import json
    import os

    import joblib

    model_dir = "data/processed/sdm_models"
    if not os.path.exists(model_dir):
        return

    try:
        from src.services.species_mapping import COMMON_TO_SCIENTIFIC

        # Count trip log catches per species (productive stops only)
        trip_counts: dict[str, int] = {}
        stops = list(
            db.execute("""
            SELECT species_caught FROM stops WHERE was_productive = 1
        """).fetchall()
        )

        for (sc_json,) in stops:
            species_list = json.loads(sc_json or "[]")
            for common in species_list:
                clean = common.lower().replace("(uncertain)", "").strip()
                sci = COMMON_TO_SCIENTIFIC.get(clean)
                if sci:
                    trip_counts[sci] = trip_counts.get(sci, 0) + 1

        # Compare against model training data
        retrain_candidates = []
        for f in os.listdir(model_dir):
            if not f.endswith(".joblib"):
                continue
            b = joblib.load(os.path.join(model_dir, f))
            species = b.get("species")
            n_inat = b.get("n_inat", 0) or 0
            n_gbif = b.get("n_gbif", 0) or 0
            n_trip = b.get("n_trip_log", 0) or 0
            current_trip = trip_counts.get(species, 0)
            baseline = n_inat + n_gbif

            if baseline > 0 and current_trip > 0:
                threshold = baseline * 0.20
                new_points = current_trip - n_trip
                if new_points >= threshold:
                    retrain_candidates.append((species, new_points, baseline))

        if retrain_candidates:
            console.print("\n[yellow][SDM] Retraining recommended for:[/yellow]")
            for species, new_pts, baseline in retrain_candidates:
                pct = int(new_pts / baseline * 100)
                console.print(
                    f"  [yellow]{species}: +{new_pts} new trip log points "
                    f"({pct}% of {baseline} iNat+GBIF records)[/yellow]"
                )
            console.print("[yellow][SDM] Run 'uv run fishbot train-sdm' to retrain.[/yellow]")
        else:
            console.print(
                "[dim][SDM] No retraining needed — trip log growth below threshold.[/dim]"
            )

    except Exception as e:
        console.print(f"[dim][SDM] Retrain check failed: {e}[/dim]")


def _print_profile(p: UserProfile) -> None:
    home = p.home_location
    home_str = f"{home.name} ({home.lat}, {home.lng})" if home else "(not set)"
    species = ", ".join(p.target_species) or "(none)"
    body_lines = [
        f"Home jurisdiction: {p.home_jurisdiction}",
        f"Home location: {home_str}",
        f"Target species: {species}",
        f"Fishing style: {p.fishing_style or '(not set)'}",
        f"Skill level: {p.skill_level}",
        f"Preferences: {p.preferences or '(none)'}",
    ]
    if p.frequented_jurisdictions:
        body_lines.append(f"Also fishes: {', '.join(p.frequented_jurisdictions)}")
    if p.gear:
        body_lines.append(f"Gear: {p.gear}")
    if p.budget is not None:
        body_lines.append(f"Annual budget: ${p.budget}")
    console.print(Panel("\n".join(body_lines), title="Profile", border_style="cyan"))


@app.command(name="ingest-trca")
def ingest_trca() -> None:
    """Ingest TRCA fish community survey data (abundance + sampling events)."""
    import importlib

    from src.storage.fish_surveys import upsert_fish_surveys

    trca = importlib.import_module("src.ingest.jurisdictions.ca_on.trca_surveys")
    url = trca.resolve_csv_url()
    if not url:
        console.print("[red]Could not resolve the TRCA fish community CSV.[/red]")
        console.print("[dim]Run with -v: discovery logs the available resource names.[/dim]")
        raise typer.Exit(1)

    path = trca.download_survey_csv(url)
    if path is None:
        console.print("[red]Download failed.[/red]")
        raise typer.Exit(1)

    records = trca.parse_survey_records(path, source_url=url)
    db = get_db()
    written = upsert_fish_surveys(db, records)

    n_events = db.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM fish_surveys GROUP BY station_name, visit_date)"
    ).fetchone()[0]

    table = Table(title="TRCA fish community survey")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("records parsed", str(len(records)))
    table.add_row("new rows written", str(written))
    table.add_row("rows in table", str(db["fish_surveys"].count))
    table.add_row("sampling events", str(n_events))
    console.print(table)


@app.command(name="harvest-ca")
def harvest_ca() -> None:
    """Probe Conservation Authority portals for publishable fish data.

    Discovery only -- writes no records. Free to run on a schedule: plain HTTP,
    no model calls. The report names any authority publishing fish data that has
    no adapter yet, which is the only part needing a human.
    """
    import importlib

    h = importlib.import_module("src.ingest.jurisdictions.ca_on.ca_harvest")
    results = h.harvest()
    path = h.write_report(results)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    meanings = {
        "adapted": "adapter exists",
        "catalogue_found": "catalogue responds, not yet adapted",
        "no_catalogue": "site up, no machine-readable index",
        "needs_url": "[yellow]unchecked[/yellow] - no website recorded",
    }
    table = Table(title="Conservation Authority portal harvest")
    table.add_column("status")
    table.add_column("count", justify="right")
    table.add_column("meaning")
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        table.add_row(status, str(n), meanings.get(status, ""))
    console.print(table)

    actionable = [r for r in results if r.status == "catalogue_found" and r.fish_datasets]
    if actionable:
        console.print()
        console.print("[green]Publishes fish data, no adapter yet:[/green]")
        for r in actionable:
            console.print(f"  {r.name}")
            for d in r.fish_datasets[:5]:
                console.print(f"    [dim]{d}[/dim]")
    else:
        console.print()
        console.print("[dim]No newly adaptable sources found.[/dim]")
    console.print(f"[dim]Report: {path}[/dim]")


@app.command(name="build-stretches")
def build_stretches_cmd(
    curation: str = typer.Option(
        "data/curation/stretches_ca_on.toml", "--curation", help="Curation file to build from."
    ),
    show_candidates: int = typer.Option(
        10, "--candidates", help="How many uncovered candidates to print as curation entries."
    ),
) -> None:
    """Rebuild the explore map's named fishing stretches from the curation file.

    Fetches the OHN network for the file's build area (cached 30 days), traces
    every curated stretch through it, and replaces the stored set. Exits 1 if
    any curated stretch failed to build.
    """
    from pathlib import Path

    from src.services.stretches import build_stretches, candidate_toml

    report = build_stretches(get_db(), Path(curation))

    table = Table(title=f"Fishing stretches — {report.jurisdiction}")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("network segments", f"{report.network_segments:,}")
    table.add_row("curated stretches", str(report.curated_total))
    table.add_row("built", str(len(report.built)))
    table.add_row("disabled", str(len(report.disabled)))
    table.add_row("failed", str(len(report.failed)))
    table.add_row("segments assigned", f"{report.segments_assigned:,}")
    table.add_row("candidates clustered", str(report.candidates_total))
    table.add_row("candidates not covered", str(len(report.uncovered_candidates)))
    console.print(table)

    for issue in report.issues:
        colour = "red" if issue.blocking else "yellow"
        console.print(f"[{colour}]{issue.stretch_id}: {issue.kind}[/{colour}] {issue.detail}")

    if report.uncovered_candidates and show_candidates > 0:
        console.print()
        console.print(
            "[dim]Largest clustered candidates with no curated stretch "
            "(paste into the curation file to add one):[/dim]"
        )
        for cand in report.uncovered_candidates[:show_candidates]:
            console.print(
                f"[dim]# {cand.length_km:.1f} km, {cand.upstream_km:,.0f} km of channel "
                f"upstream[/dim]"
            )
            console.print(candidate_toml(cand), markup=False, highlight=False)

    if report.failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
