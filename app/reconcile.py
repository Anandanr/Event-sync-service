"""Reconciliation engine.

Joins records that refer to the same real-world meeting, detects conflicting
field values, and flags same-source near-duplicates.

Design notes
------------
There is no shared ID between the sources, so matching is *scored* rather than
keyed. Every accepted match records the signals that produced it, which lets the
UI explain *why* two records were joined. Nothing is merged on a single weak
signal: a same-day gate plus a minimum score is required.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from .models import (
    FieldCandidate,
    NormalizedEvent,
    UnifiedField,
    UnifiedMeeting,
)

# --- Matching thresholds -----------------------------------------------------
# Two records must fall on the same calendar day to be considered at all.
MAX_TIME_DELTA = timedelta(hours=3)
STRONG_TIME_DELTA = timedelta(minutes=30)
# Below this combined score we treat the pair as unrelated.
MATCH_THRESHOLD = 0.55
# Same-source records this close in time with overlapping attendees are flagged
# as possible duplicates (CAL-A5 / CAL-A6) but never auto-merged.
DUPLICATE_WINDOW = timedelta(hours=2)

# --- Field precedence --------------------------------------------------------
# When both sources supply a field and disagree, one value is chosen for display.
# CRM is authoritative for relationship data; Calendar is authoritative for
# scheduling data. The losing value is never discarded - it stays in candidates.
PRECEDENCE: dict[str, list[str]] = {
    "title": ["crm", "calendar"],
    "start_time": ["calendar", "crm"],
    "end_time": ["calendar", "crm"],
    "location": ["crm", "calendar"],
    "meeting_type": ["crm", "calendar"],
    "status": ["crm", "calendar"],
    "client_name": ["crm", "calendar"],
    "client_company": ["crm", "calendar"],
    "relationship_owner": ["crm", "calendar"],
    "organizer": ["calendar", "crm"],
    "attendees": ["calendar", "crm"],
    "notes": ["crm", "calendar"],
}

_STOPWORDS = {
    "the", "a", "an", "and", "with", "for", "of", "to", "on", "in", "at",
    "meeting", "call", "session", "review", "discussion", "update",
}

# Words that carry no discriminating power for location matching.
_LOCATION_NOISE = re.compile(
    r"\b(hq|office|room|virtual|zoom|teams|microsoft|https?|us|j)\b|[\W_]+",
    re.IGNORECASE,
)

# Fields that are expected to differ between systems and therefore should not be
# reported as conflicts. A CRM "note" and a calendar "description" are different
# artefacts; an owner's display name and their email are the same fact expressed
# differently. Reporting these as conflicts would drown out the real ones.
NON_CONFLICTING_FIELDS = {"notes", "organizer", "attendees"}


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _location_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    cleaned = _LOCATION_NOISE.sub(" ", text.lower())
    return {w for w in cleaned.split() if len(w) > 2}


def _emails(event: NormalizedEvent) -> set[str]:
    found = {a.lower() for a in event.attendees if "@" in a}
    if event.organizer and "@" in event.organizer:
        found.add(event.organizer.lower())
    return found


def _names(event: NormalizedEvent) -> set[str]:
    """Person/company tokens we can use to link a CRM client to calendar data."""
    parts: list[str] = []
    for value in (event.client_name, event.client_company, event.relationship_owner):
        if value:
            parts.append(value)
    return _tokens(" ".join(parts))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_pair(crm: NormalizedEvent, calendar: NormalizedEvent) -> tuple[float, list[str]]:
    """Score how likely two cross-source records describe the same meeting.

    Returns a confidence in ``[0, 1]`` and the list of contributing signals.
    """
    if crm.start_time is None or calendar.start_time is None:
        return 0.0, []

    if crm.start_time.date() != calendar.start_time.date():
        return 0.0, []

    score = 0.0
    reasons: list[str] = ["same_day"]

    delta = abs(crm.start_time - calendar.start_time)
    if delta <= STRONG_TIME_DELTA:
        score += 0.40
        reasons.append("time_within_30min")
    elif delta <= MAX_TIME_DELTA:
        # Partial credit that decays with distance.
        fraction = 1 - (delta / MAX_TIME_DELTA)
        score += 0.40 * fraction
        reasons.append(f"time_within_{int(delta.total_seconds() // 60)}min")
    elif crm.raw.get("meeting_time") is None:
        # The CRM record has no time at all, so time cannot be used as evidence
        # either way. We award neutral credit and let name/title signals decide,
        # which is what links CRM-1007 to CAL-A8 (both Meridian follow-ups).
        score += 0.15
        reasons.append("crm_time_missing")

    # Attendee emails vs CRM client/owner names. The calendar uses
    # "first.last@domain", so we compare on name tokens.
    attendee_tokens: set[str] = set()
    for email in _emails(calendar):
        attendee_tokens |= _tokens(email.split("@")[0].replace(".", " "))
    name_overlap = _jaccard(_names(crm), attendee_tokens)
    if name_overlap > 0:
        score += 0.30 * min(1.0, name_overlap * 3)
        reasons.append("attendee_name_overlap")

    title_overlap = _jaccard(_tokens(crm.title), _tokens(calendar.title))
    if title_overlap > 0:
        score += 0.20 * min(1.0, title_overlap * 2)
        reasons.append("title_overlap")

    location_overlap = _jaccard(
        _location_tokens(crm.location), _location_tokens(calendar.location)
    )
    if location_overlap > 0:
        score += 0.10 * min(1.0, location_overlap * 2)
        reasons.append("location_overlap")

    return min(score, 1.0), reasons


def _best_matching(crm_events, calendar_events):
    """Greedy global matching: repeatedly take the highest-scoring available pair.

    Greedy-by-score avoids the classic failure where the first record processed
    claims a calendar event that a later record matches far better.
    """
    candidates: list[tuple[float, list[str], str, str]] = []
    for crm in crm_events:
        for calendar in calendar_events:
            score, reasons = score_pair(crm, calendar)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, reasons, crm.source_id, calendar.source_id))

    candidates.sort(key=lambda item: item[0], reverse=True)

    used_crm: set[str] = set()
    used_cal: set[str] = set()
    matches: dict[str, tuple[str, float, list[str]]] = {}
    for score, reasons, crm_id, cal_id in candidates:
        if crm_id in used_crm or cal_id in used_cal:
            continue
        used_crm.add(crm_id)
        used_cal.add(cal_id)
        matches[crm_id] = (cal_id, score, reasons)
    return matches


def _field_value(event: NormalizedEvent, field: str):
    return getattr(event, field, None)


def _build_fields(events: list[NormalizedEvent]) -> dict[str, UnifiedField]:
    """Merge one or more source records into a field map with conflict detection."""
    fields: dict[str, UnifiedField] = {}
    for field, order in PRECEDENCE.items():
        candidates: list[FieldCandidate] = []
        for event in events:
            value = _field_value(event, field)
            if value in (None, "", [], {}):
                continue
            candidates.append(
                FieldCandidate(
                    source=event.source, source_id=event.source_id, value=value
                )
            )
        if not candidates:
            continue

        # Conflict = two sources disagree on a non-empty value. Fields that are
        # expected to differ across systems are exempt (see NON_CONFLICTING_FIELDS).
        distinct = {_comparable(c.value) for c in candidates}
        conflict = len(distinct) > 1 and field not in NON_CONFLICTING_FIELDS

        ordered = sorted(
            candidates, key=lambda c: order.index(c.source) if c.source in order else 99
        )
        fields[field] = UnifiedField(
            value=ordered[0].value, conflict=conflict, candidates=candidates
        )
    return fields


def _comparable(value):
    """Normalize a value for conflict comparison (case/whitespace-insensitive)."""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip().lower())
    if isinstance(value, list):
        return tuple(sorted(str(v).lower() for v in value))
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _flag_same_source_duplicates(events: list[NormalizedEvent]) -> dict[str, str]:
    """Find near-duplicate records *within* one source.

    Kept deliberately conservative: same source, close in time, and sharing at
    least one attendee *and* a title token. Proximity alone is not enough - two
    unrelated meetings can be scheduled back to back (e.g. the recurring
    "Internal Pipeline Review" and "Client Reception" 2h apart on 2025-03-14).
    These are flagged for a human, never auto-merged.
    """
    duplicates: dict[str, str] = {}
    for source in ("crm", "calendar"):
        group = [e for e in events if e.source == source and e.start_time]
        for i, first in enumerate(group):
            for second in group[i + 1:]:
                delta = abs(first.start_time - second.start_time)
                if delta > DUPLICATE_WINDOW:
                    continue
                shared_attendees = _emails(first) & _emails(second)
                shared_title = _tokens(first.title) & _tokens(second.title)
                if shared_attendees and shared_title:
                    duplicates[second.source_id] = first.source_id
                    duplicates[first.source_id] = second.source_id
    return duplicates


def reconcile(events: list[NormalizedEvent]) -> list[UnifiedMeeting]:
    """Full reconciliation pipeline: match, merge, detect conflicts and duplicates."""
    crm_events = [e for e in events if e.source == "crm"]
    calendar_events = [e for e in events if e.source == "calendar"]
    by_id = {e.source_id: e for e in events}

    matches = _best_matching(crm_events, calendar_events)
    duplicates = _flag_same_source_duplicates(events)

    meetings: list[UnifiedMeeting] = []
    consumed: set[str] = set()

    for crm_id, (cal_id, score, reasons) in matches.items():
        crm = by_id[crm_id]
        calendar = by_id[cal_id]
        consumed.update({crm_id, cal_id})
        meetings.append(
            _assemble(
                meeting_id=f"meeting-{crm_id}-{cal_id}",
                events=[crm, calendar],
                confidence=score,
                reasons=reasons,
                duplicates=duplicates,
            )
        )

    # One-sided records are kept - they are part of the unified view.
    for event in events:
        if event.source_id in consumed:
            continue
        meetings.append(
            _assemble(
                meeting_id=f"meeting-{event.source_id}",
                events=[event],
                confidence=1.0,
                reasons=["single_source"],
                duplicates=duplicates,
            )
        )

    meetings.sort(key=lambda m: (m.start_time is None, m.start_time or datetime.max))
    return meetings


def _assemble(meeting_id, events, confidence, reasons, duplicates) -> UnifiedMeeting:
    fields = _build_fields(events)
    sources = sorted({e.source for e in events})
    flags: list[str] = []
    for event in events:
        flags.extend(event.quality_flags)

    duplicate_of = None
    for event in events:
        if event.source_id in duplicates:
            duplicate_of = duplicates[event.source_id]
            break

    start = fields.get("start_time")
    end = fields.get("end_time")
    title = fields.get("title")

    return UnifiedMeeting(
        id=meeting_id,
        title=title.value if title else None,
        start_time=start.value if start else None,
        end_time=end.value if end else None,
        sources=sources,
        source_ids={e.source: e.source_id for e in events},
        match_confidence=round(confidence, 3),
        match_reasons=reasons,
        fields=fields,
        data_quality_flags=sorted(set(flags)),
        possible_duplicate_of=duplicate_of,
    )
