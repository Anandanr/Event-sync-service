"""In-memory store for the reconciled meeting set.

The dataset is tiny and static, so a dict is the right tool: no persistence
layer to justify, no migration story, and re-ingestion is instant. Swapping in
SQLite later would only require reimplementing this module's interface.
"""

from __future__ import annotations

from pathlib import Path

from .ingest import ingest_all
from .models import Stats, UnifiedMeeting
from .reconcile import reconcile


class MeetingStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._meetings: list[UnifiedMeeting] = []
        self.refresh()

    def refresh(self) -> None:
        events = ingest_all(self._data_dir)
        self._meetings = reconcile(events)

    @property
    def meetings(self) -> list[UnifiedMeeting]:
        return self._meetings

    def get(self, meeting_id: str) -> UnifiedMeeting | None:
        return next((m for m in self._meetings if m.id == meeting_id), None)

    def stats(self) -> Stats:
        matched = sum(1 for m in self._meetings if len(m.sources) > 1)
        crm_only = sum(1 for m in self._meetings if m.sources == ["crm"])
        cal_only = sum(1 for m in self._meetings if m.sources == ["calendar"])
        with_conflicts = [m for m in self._meetings if _conflicts(m)]
        total_conflicts = sum(len(_conflicts(m)) for m in self._meetings)
        flagged = sum(1 for m in self._meetings if m.data_quality_flags)
        duplicates = sum(1 for m in self._meetings if m.possible_duplicate_of)
        unmatched = sum(
            1
            for m in self._meetings
            if len(m.sources) == 1 and "single_source" in m.match_reasons
            and not m.possible_duplicate_of
        )
        return Stats(
            total_meetings=len(self._meetings),
            matched=matched,
            crm_only=crm_only,
            calendar_only=cal_only,
            meetings_with_conflicts=len(with_conflicts),
            total_conflicts=total_conflicts,
            records_with_quality_flags=flagged,
            possible_duplicates=duplicates,
            unmatched_source_records=unmatched,
        )


def _conflicts(meeting: UnifiedMeeting) -> dict[str, object]:
    return {name: field for name, field in meeting.fields.items() if field.conflict}
