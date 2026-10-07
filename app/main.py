"""REST API for the Event Sync Service."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import Stats, UnifiedMeeting
from .store import MeetingStore

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Event Sync Service",
    description=(
        "Reconciles CRM and Calendar meeting records into a unified view, "
        "preserving per-field provenance and surfacing conflicts."
    ),
    version="1.0.0",
)

store = MeetingStore(DATA_DIR)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meetings", response_model=list[UnifiedMeeting])
def list_meetings(
    source: str | None = Query(
        default=None,
        description="Filter to meetings involving a source: 'crm' or 'calendar'.",
    ),
    conflicts_only: bool = Query(
        default=False, description="Only return meetings with conflicting fields."
    ),
    date_from: date | None = Query(default=None, description="Inclusive start date."),
    date_to: date | None = Query(default=None, description="Inclusive end date."),
) -> list[UnifiedMeeting]:
    results = store.meetings

    if source:
        if source not in ("crm", "calendar"):
            raise HTTPException(status_code=400, detail="source must be 'crm' or 'calendar'")
        results = [m for m in results if source in m.sources]

    if conflicts_only:
        results = [m for m in results if any(f.conflict for f in m.fields.values())]

    if date_from:
        results = [m for m in results if m.start_time and m.start_time.date() >= date_from]
    if date_to:
        results = [m for m in results if m.start_time and m.start_time.date() <= date_to]

    return results


@app.get("/api/meetings/{meeting_id}", response_model=UnifiedMeeting)
def get_meeting(meeting_id: str) -> UnifiedMeeting:
    meeting = store.get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"meeting {meeting_id!r} not found")
    return meeting


@app.get("/api/conflicts")
def list_conflicts() -> list[dict]:
    """Every conflicting field across all meetings, flattened for easy display."""
    out: list[dict] = []
    for meeting in store.meetings:
        for name, field in meeting.fields.items():
            if not field.conflict:
                continue
            out.append(
                {
                    "meeting_id": meeting.id,
                    "title": meeting.title,
                    "field": name,
                    "chosen_value": field.value,
                    "candidates": [c.model_dump() for c in field.candidates],
                }
            )
    return out


@app.get("/api/stats", response_model=Stats)
def stats() -> Stats:
    return store.stats()


@app.post("/api/reingest")
def reingest() -> dict[str, int]:
    store.refresh()
    return {"meetings": len(store.meetings)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
