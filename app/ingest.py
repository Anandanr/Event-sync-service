"""Ingestion and normalization.

Turns the two raw JSON files into a list of :class:`NormalizedEvent`. Every
repair we make is recorded in ``quality_flags`` so the API and UI can be honest
about where the data was dirty. Nothing is ever silently dropped.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import NormalizedEvent, RawCalendarEvent, RawCrmEvent

# The CRM stores naive local times. We interpret them in the organisation's
# home timezone so that UTC-marked calendar times can be compared against them.
# This is the single most important decision in the whole pipeline: without it
# CAL-A4 (2025-03-13T19:00:00Z) would look 5 hours away from CRM-1004 (14:00).
ORG_TIMEZONE = ZoneInfo("America/New_York")

# Accepts the CRM's malformed "MM-DD/YYYY" variant, e.g. "03-15/2025".
_SLASH_DATE_RE = re.compile(r"^(\d{2})-(\d{2})/(\d{4})$")
# Accepts the calendar's "email[at]domain" obfuscation, e.g. "raj.patel[at]atlasvc.com".
_AT_OBFUSCATION_RE = re.compile(r"\[at\]", re.IGNORECASE)


def _parse_date(value: str | None, flags: list[str], label: str) -> datetime | None:
    """Parse a date-only string, tolerating the CRM's malformed variant."""
    if not value or not value.strip():
        return None
    text = value.strip()
    match = _SLASH_DATE_RE.match(text)
    if match:
        month, day, year = match.groups()
        text = f"{year}-{month}-{day}"
        flags.append(f"normalized_date:{label}={value!r}->{text!r}")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        flags.append(f"unparseable_date:{label}={value!r}")
        return None


def _parse_time(value: str | None, flags: list[str], label: str) -> time | None:
    if not value or not value.strip():
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        flags.append(f"unparseable_time:{label}={value!r}")
        return None


def _parse_datetime(value: str | None, flags: list[str], label: str) -> datetime | None:
    """Parse an ISO datetime. Naive values are assumed to be org-local.

    Values carrying an explicit UTC offset (e.g. ``...Z``) are converted into the
    org timezone so all timestamps share one frame of reference.
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        flags.append(f"unparseable_datetime:{label}={value!r}")
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ORG_TIMEZONE)
        flags.append(f"assumed_local_timezone:{label}={value!r}")
    else:
        parsed = parsed.astimezone(ORG_TIMEZONE)
        flags.append(f"converted_to_org_timezone:{label}={value!r}")
    return parsed


def _clean_email(value: str) -> tuple[str, bool]:
    """Repair the ``[at]`` obfuscation used by one calendar attendee."""
    if _AT_OBFUSCATION_RE.search(value):
        return _AT_OBFUSCATION_RE.sub("@", value), True
    return value, False


def normalize_crm(raw: RawCrmEvent) -> NormalizedEvent:
    flags: list[str] = []
    date_part = _parse_date(raw.meeting_date, flags, "meeting_date")
    time_part = _parse_time(raw.meeting_time, flags, "meeting_time")

    start: datetime | None = None
    if date_part is not None:
        start = date_part.replace(
            hour=time_part.hour, minute=time_part.minute
        ) if time_part else date_part
        start = start.replace(tzinfo=ORG_TIMEZONE)
    if raw.meeting_time is None:
        flags.append("missing_field:meeting_time")
    if raw.location is None:
        flags.append("missing_field:location")
    if raw.client_name is None:
        flags.append("missing_field:client_name")
    if raw.notes is not None and raw.notes.strip() == "":
        flags.append("empty_field:notes")

    return NormalizedEvent(
        source="crm",
        source_id=raw.crm_id,
        title=raw.subject,
        start_time=start,
        end_time=None,
        location=raw.location,
        meeting_type=raw.meeting_type,
        status=raw.status,
        client_name=raw.client_name,
        client_company=raw.client_company,
        relationship_owner=raw.relationship_owner,
        organizer=raw.relationship_owner,
        attendees=[],
        notes=raw.notes,
        is_recurring=False,
        created_at=_parse_datetime(raw.created_at, flags, "created_at"),
        quality_flags=flags,
        raw=raw.model_dump(),
    )


def normalize_calendar(raw: RawCalendarEvent) -> NormalizedEvent:
    flags: list[str] = []
    start = _parse_datetime(raw.start_time, flags, "start_time")
    end = _parse_datetime(raw.end_time, flags, "end_time")

    attendees: list[str] = []
    for attendee in raw.attendees or []:
        cleaned, repaired = _clean_email(attendee)
        if repaired:
            flags.append(f"repaired_email:{attendee!r}->{cleaned!r}")
        attendees.append(cleaned)
    if not attendees:
        flags.append("missing_field:attendees")
    if raw.location is None:
        flags.append("missing_field:location")
    if raw.description is None:
        flags.append("missing_field:description")

    return NormalizedEvent(
        source="calendar",
        source_id=raw.event_id,
        title=raw.title,
        start_time=start,
        end_time=end,
        location=raw.location,
        meeting_type=None,
        status=raw.status,
        client_name=None,
        client_company=None,
        relationship_owner=None,
        organizer=raw.organizer,
        attendees=attendees,
        notes=raw.description,
        is_recurring=bool(raw.is_recurring),
        created_at=_parse_datetime(raw.created_at, flags, "created_at"),
        quality_flags=flags,
        raw=raw.model_dump(),
    )


def load_source(path: Path, source: str) -> list[NormalizedEvent]:
    """Load and normalize one source file."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if source == "crm":
        return [normalize_crm(RawCrmEvent(**record)) for record in payload]
    if source == "calendar":
        return [normalize_calendar(RawCalendarEvent(**record)) for record in payload]
    raise ValueError(f"unknown source: {source}")


def ingest_all(data_dir: Path) -> list[NormalizedEvent]:
    """Load and normalize both sources into a single list."""
    events: list[NormalizedEvent] = []
    events.extend(load_source(data_dir / "crm_events.json", "crm"))
    events.extend(load_source(data_dir / "calendar_events.json", "calendar"))
    return events
