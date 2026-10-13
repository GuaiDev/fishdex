"""Species context — the local species file, routed through ContextField.

This file previously bypassed the provenance system entirely: `fishing_notes`
and three conservation statuses went straight from a JSON blob to a tool result
the model treated as retrieved fact. The text was generated during development,
not gathered from a source, and carried no marker saying so.

Everything here is therefore tagged INFERENCE until a real registry is cited.
That is not a hedge — INFERENCE is defined as "reasoning only, no source", and
model-generated text is exactly that. A verified status upgrades to RECORD with
its citation attached, and only then can the conservation flag clear.
"""

import logging

from sqlite_utils import Database

from src.models.context import (
    ContextField,
    EmptyReason,
    SpeciesContext,
)

logger = logging.getLogger(__name__)

_UNVERIFIED = (
    "generated during development, not checked against COSEWIC or the SARA "
    "registry — treat as a hypothesis, not a record"
)

_LISTED = frozenset({"Special Concern", "Threatened", "Endangered", "Extirpated"})


def status_known_listed(sr) -> bool:
    """An affirmative listing signal from any authority, verified or not.

    The one definition of the gate. `describe_species` reports it and the spot
    card enforces it, so the two cannot disagree about which fish are protected.
    """
    return bool({sr.sara_status, sr.ontario_status, sr.cosewic_status} & _LISTED)


def listing_check(
    db: Database, scientific_name: str | None, common_name: str | None
) -> bool | None:
    """Is this recorded species affirmatively listed? None when we cannot tell.

    None means the species is not in the local species file, so nothing is
    known either way. It is reported rather than rounded to False so a caller
    can count how many species it could not check; `sar_alert` is deliberately
    not consulted, because it is true for every species in the file.
    """
    from src.storage.species_ranges import query_species_range_exact

    try:
        sr = query_species_range_exact(db, common_name=common_name, scientific_name=scientific_name)
    except Exception:  # noqa: BLE001 - never break the caller over this slice
        logger.warning("species listing check failed", exc_info=True)
        return None
    return None if sr is None else status_known_listed(sr)


def describe_species(db: Database, name: str) -> SpeciesContext:
    """Everything the corpus holds about a species, with provenance on each claim.

    Fails closed: an unverified conservation status raises the flag rather than
    clearing it, because the cost of a false "safe" is someone targeting a
    protected fish.
    """
    try:
        from src.storage.species_ranges import query_species_range

        sr = query_species_range(db, name)
    except Exception:  # noqa: BLE001 - never break the caller over this slice
        logger.warning("species range lookup failed", exc_info=True)
        sr = None

    if sr is None:
        return SpeciesContext(
            species=name,
            found=False,
            conservation_status=ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA),
            habitat_note=ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA),
            angling_note=ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA),
            native_to_ontario=ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA),
            sar_reason=(
                "Species not in the local file, so nothing is known about its "
                "conservation status. Treated as potentially listed."
            ),
        )

    verified = sr.status_is_verified
    status = sr.sara_status or sr.ontario_status or sr.cosewic_status

    # An affirmative listing signal from any authority, verified or not. An
    # unverified "Endangered" is still a reason to refuse targeting guidance;
    # an unverified "Not at Risk" is not a reason to grant it, but it is also
    # not evidence of a listing. Callers that need a hard refusal gate on this
    # rather than on `sar_alert`, which is true for everything until the
    # registry import runs.
    known_listed = status_known_listed(sr)

    if verified:
        listed = {sr.sara_status, sr.ontario_status, sr.cosewic_status} & _LISTED
        alert = bool(listed)
        reason = (
            f"Listed: {', '.join(sorted(listed))} (source: {sr.status_source})."
            if listed
            else f"Verified not at risk (source: {sr.status_source})."
        )
        # Two dates, and they are years apart. The assessment date is when the
        # registry decided; the checked date is when we last read the file.
        # Only the first tells an angler anything about the fish, so it is the
        # one that goes in the provenance slot — the second travels as the
        # "so what" and is labelled, because a bare date next to a source reads
        # as the source's date.
        checked = (
            sr.status_last_checked_at.date().isoformat()
            if sr.status_last_checked_at
            else None
        )
        assessed = sr.status_assessed_on
        if assessed:
            meaning = f"assessed {assessed}"
            if checked:
                meaning += f"; we last read the registry {checked}"
        elif checked:
            meaning = (
                f"the registry did not publish an assessment date; we last "
                f"read it {checked}"
            )
        else:
            meaning = None

        status_field = ContextField.recorded(
            status,
            source=sr.status_source or "registry",
            date=assessed or checked,
            meaning=meaning,
        )
    else:
        alert = True
        reason = (
            "Conservation status has not been verified against COSEWIC or the SARA "
            "registry. Treated as potentially listed; targeting guidance withheld."
        )
        status_field = ContextField.inferred(status, meaning=_UNVERIFIED)

    return SpeciesContext(
        species=sr.species,
        scientific_name=sr.scientific_name,
        conservation_status=status_field,
        habitat_note=(
            ContextField.inferred(sr.habitat_notes, meaning=_UNVERIFIED)
            if sr.habitat_notes
            else ContextField.empty(EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE)
        ),
        angling_note=(
            # Suppressed entirely while the species may be listed — the whole
            # point of failing closed is to not hand out targeting advice.
            # This used to report SOURCE_DOES_NOT_COVER_AREA, which described a
            # deliberate policy decision as a hole in the data. The source
            # covers it fine and the note exists; we are choosing not to show
            # it, and the two have opposite remedies.
            ContextField.empty(EmptyReason.SUPPRESSED_ON_CONSERVATION_GROUNDS)
            if alert
            else ContextField.inferred(sr.fishing_notes, meaning=_UNVERIFIED)
            if sr.fishing_notes
            else ContextField.empty(EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE)
        ),
        native_to_ontario=ContextField.inferred(
            sr.native_to_ontario, meaning=_UNVERIFIED
        ),
        sar_alert=alert,
        sar_reason=reason,
        targeting_guidance_suppressed=alert,
        status_known_listed=known_listed,
    )
