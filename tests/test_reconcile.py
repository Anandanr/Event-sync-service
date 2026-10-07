"""Tests for the reconciliation pipeline.

Focus is deliberately narrow: the edge cases that the assessment data was built
to exercise. Each test names the specific trap it guards against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest import ingest_all, normalize_calendar, normalize_crm
from app.models import RawCalendarEvent, RawCrmEvent
from app.reconcile import reconcile, score_pair
from app.store import MeetingStore

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def store() -> MeetingStore:
    return MeetingStore(DATA_DIR)


@pytest.fixture(scope="module")
def events():
    return {e.source_id: e for e in ingest_all(DATA_DIR)}


def test_malformed_crm_date_is_repaired(events):
    """CRM-1008 uses 'MM-DD/YYYY'; it must normalize to 2025-03-15."""
    event = events["CRM-1008"]
    assert event.start_time is not None
    assert event.start_time.date().isoformat() == "2025-03-15"
    assert any("normalized_date" in f for f in event.quality_flags)


def test_utc_calendar_time_matches_naive_crm_time(events):
    """CAL-A4 is 19:00Z == 15:00 EDT, one hour from CRM-1004's 14:00.

    This guards the timezone trap: a naive UTC-vs-local comparison would place
    the records 5 hours apart and miss the match entirely.
    """
    crm = events["CRM-1004"]
    calendar = events["CAL-A4"]
    assert crm.start_time.hour == 14
    assert calendar.start_time.hour == 15
    score, reasons = score_pair(crm, calendar)
    assert score >= 0.55, reasons


def test_timezone_conversion_is_recorded(events):
    flags = events["CAL-A4"].quality_flags
    assert any("converted_to_org_timezone" in f for f in flags)


def test_malformed_email_is_repaired(events):
    """CAL-A16 contains 'raj.patel[at]atlasvc.com'."""
    attendees = events["CAL-A16"].attendees
    assert "raj.patel@atlasvc.com" in attendees
    assert any("repaired_email" in f for f in events["CAL-A16"].quality_flags)


def test_missing_time_does_not_prevent_match(store):
    """CRM-1007 has no time; it should still pair with CAL-A8 by name and title."""
    meeting = next(m for m in store.meetings if m.source_ids.get("crm") == "CRM-1007")
    assert meeting.sources == ["calendar", "crm"]
    assert "crm_time_missing" in meeting.match_reasons


def test_expected_match_count(store):
    """17 cross-source matches: the ground truth for this dataset."""
    matched = [m for m in store.meetings if len(m.sources) > 1]
    assert len(matched) == 17


def test_one_sided_records_are_retained(store):
    """Records with no counterpart must appear, not be dropped."""
    crm_only = {m.source_ids.get("crm") for m in store.meetings if m.sources == ["crm"]}
    cal_only = {
        m.source_ids.get("calendar") for m in store.meetings if m.sources == ["calendar"]
    }
    assert crm_only == {"CRM-1003", "CRM-1010", "CRM-1020"}
    # CAL-A6 is one-sided because CAL-A5 won the match; it is also flagged as a duplicate.
    assert cal_only == {"CAL-A3", "CAL-A6", "CAL-A11", "CAL-A18", "CAL-A19"}


def test_recurring_instances_are_not_merged(store):
    """CAL-A3 and CAL-A18 are separate instances of the same weekly series."""
    ids = {m.id for m in store.meetings}
    assert "meeting-CAL-A3" in ids
    assert "meeting-CAL-A18" in ids


def test_same_source_duplicate_is_flagged_not_merged(store):
    """CAL-A5 / CAL-A6 look like a duplicate pair; they must be flagged only."""
    a5 = next(m for m in store.meetings if m.source_ids.get("calendar") == "CAL-A5")
    a6 = next(m for m in store.meetings if m.source_ids.get("calendar") == "CAL-A6")
    assert a5.possible_duplicate_of == "CAL-A6"
    assert a6.possible_duplicate_of == "CAL-A5"
    assert a5.sources == ["calendar", "crm"]  # A5 matched the CRM record
    assert a6.sources == ["calendar"]  # A6 was not merged into it


def test_unrelated_same_day_records_are_not_flagged_as_duplicates(store):
    """CAL-A7 (16:00) and CAL-A11 (18:00) are different meetings on 2025-03-14."""
    a7 = next(m for m in store.meetings if m.source_ids.get("calendar") == "CAL-A7")
    assert a7.possible_duplicate_of is None


def test_location_conflict_is_detected(store):
    """CRM-1002 says NYC Office; CAL-A2 says Zoom. That is a real conflict."""
    meeting = next(m for m in store.meetings if m.source_ids.get("crm") == "CRM-1002")
    assert meeting.fields["location"].conflict is True
    sources = {c.source for c in meeting.fields["location"].candidates}
    assert sources == {"crm", "calendar"}


def test_crm_wins_location_precedence(store):
    meeting = next(m for m in store.meetings if m.source_ids.get("crm") == "CRM-1002")
    assert meeting.fields["location"].value == "NYC Office - 30th Floor"


def test_expected_conflict_count(store):
    """30 conflicting fields across the dataset."""
    assert store.stats().total_conflicts == 30


def test_notes_differences_are_not_conflicts(store):
    """A CRM note and a calendar description are different artefacts."""
    meeting = next(m for m in store.meetings if m.source_ids.get("crm") == "CRM-1001")
    assert meeting.fields["notes"].conflict is False


def test_no_record_is_silently_dropped(store):
    """Every one of the 42 source records is represented in exactly one meeting."""
    crm_ids = {m.source_ids.get("crm") for m in store.meetings if "crm" in m.source_ids}
    cal_ids = {
        m.source_ids.get("calendar")
        for m in store.meetings
        if "calendar" in m.source_ids
    }
    assert len(crm_ids) == 20
    assert len(cal_ids) == 22


def test_quality_flags_present_for_dirty_records(store):
    assert store.stats().records_with_quality_flags > 0


def test_normalize_crm_handles_null_fields():
    """Missing fields must not raise; they become flags."""
    event = normalize_crm(RawCrmEvent(crm_id="X", meeting_date="2025-01-01"))
    assert event.start_time is not None
    assert "missing_field:meeting_time" in event.quality_flags


def test_normalize_calendar_handles_empty_attendees():
    event = normalize_calendar(RawCalendarEvent(event_id="X", start_time="2025-01-01T10:00:00"))
    assert event.attendees == []
    assert "missing_field:attendees" in event.quality_flags


def test_same_day_is_a_hard_gate():
    """Records on different days must never match, however similar."""
    crm = normalize_crm(
        RawCrmEvent(crm_id="A", subject="Review", meeting_date="2025-03-10", meeting_time="10:00")
    )
    cal = normalize_calendar(
        RawCalendarEvent(event_id="B", title="Review", start_time="2025-03-11T10:00:00")
    )
    score, _ = score_pair(crm, cal)
    assert score == 0.0
