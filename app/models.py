"""Data models for the Event Sync Service.

Raw models mirror the upstream schemas exactly (all fields optional/loose, because
the sources contain missing and malformed values). Normalized models are what the
ingestion layer produces after cleaning. Unified models are what the reconciliation
layer produces and what the API serves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceName = Literal["crm", "calendar"]


class RawCrmEvent(BaseModel):
    """A record from the CRM API, as-is. Fields are loose on purpose."""

    crm_id: str
    subject: str | None = None
    client_name: str | None = None
    client_company: str | None = None
    relationship_owner: str | None = None
    meeting_date: str | None = None
    meeting_time: str | None = None
    meeting_type: str | None = None
    location: str | None = None
    notes: str | None = None
    status: str | None = None
    created_at: str | None = None


class RawCalendarEvent(BaseModel):
    """A record from the Calendar API, as-is. Fields are loose on purpose."""

    event_id: str
    title: str | None = None
    organizer: str | None = None
    attendees: list[str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    is_recurring: bool | None = None
    status: str | None = None
    created_at: str | None = None


class NormalizedEvent(BaseModel):
    """A single source record after cleaning, with provenance preserved.

    ``raw`` always holds the untouched original so the UI can show exactly what
    each upstream system said, even when we repaired a value.
    """

    source: SourceName
    source_id: str
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    meeting_type: str | None = None
    status: str | None = None
    client_name: str | None = None
    client_company: str | None = None
    relationship_owner: str | None = None
    organizer: str | None = None
    attendees: list[str] = Field(default_factory=list)
    notes: str | None = None
    is_recurring: bool = False
    created_at: datetime | None = None
    # Human-readable notes about what we changed while normalizing.
    quality_flags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class FieldCandidate(BaseModel):
    """One source's opinion about a field's value."""

    source: SourceName
    source_id: str
    value: Any = None


class UnifiedField(BaseModel):
    """A resolved field plus every source's candidate value.

    ``conflict`` is true when two or more sources supplied *different* non-empty
    values. ``value`` is the display value chosen by the precedence rules.
    """

    value: Any = None
    conflict: bool = False
    candidates: list[FieldCandidate] = Field(default_factory=list)


class UnifiedMeeting(BaseModel):
    """The reconciled, source-agnostic view served to clients."""

    id: str
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    sources: list[SourceName] = Field(default_factory=list)
    source_ids: dict[str, str] = Field(default_factory=dict)
    match_confidence: float = 1.0
    match_reasons: list[str] = Field(default_factory=list)
    fields: dict[str, UnifiedField] = Field(default_factory=dict)
    data_quality_flags: list[str] = Field(default_factory=list)
    possible_duplicate_of: str | None = None


class Stats(BaseModel):
    """Summary counts for the dashboard header."""

    total_meetings: int
    matched: int
    crm_only: int
    calendar_only: int
    meetings_with_conflicts: int
    total_conflicts: int
    records_with_quality_flags: int
    possible_duplicates: int
    unmatched_source_records: int
