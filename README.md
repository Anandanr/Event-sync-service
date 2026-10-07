# Event Sync Service

A small service that ingests meeting records from two upstream systems (a CRM API
and a Calendar API), reconciles records that refer to the same real-world meeting,
and serves a unified view through a REST API and a web UI.

The core idea: **no source is thrown away and no decision is hidden.** Every field
in the unified view records which source it came from, every conflict is surfaced
rather than silently resolved, and every repair made during ingestion is logged.

---

## Quick start

Requires Python 3.10+ (developed on 3.13). No other prerequisites.

```bash
./run.sh
```

That single command creates a virtualenv on first run, installs dependencies, and
starts the server. Then open:

- **UI:** http://127.0.0.1:8000
- **API docs (auto-generated):** http://127.0.0.1:8000/docs

To run the tests:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

### Manual start (if you prefer)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

---

## Architecture

```
data/crm_events.json ─┐
                      ├─► ingest.py ──► reconcile.py ──► store.py ──► main.py (FastAPI)
data/calendar_events.json ─┘   normalize      match+merge     in-memory      REST + UI
```

| Module | Responsibility |
|---|---|
| `app/models.py` | Pydantic models: raw source records, normalized records, unified meetings |
| `app/ingest.py` | Loads both JSON files and normalizes dates, times, timezones, emails |
| `app/reconcile.py` | Scored matching, field merging, conflict detection, duplicate flagging |
| `app/store.py` | Holds the reconciled set in memory; computes stats |
| `app/main.py` | REST API and static file serving |
| `app/static/` | Dependency-free HTML/CSS/JS frontend |

The pipeline runs once at startup. `POST /api/reingest` re-runs it, which is handy
for demonstrating the pipeline without restarting the process.

---

## Approach

### 1. Normalize before matching

The two sources disagree on almost everything structural, so matching on raw values
would fail. Ingestion produces a common shape first:

- **Dates.** The CRM contains one malformed date, `"03-15/2025"` (CRM-1008). It is
  parsed and repaired to `2025-03-15`.
- **Timezones.** This is the most important normalization. CRM times are naive
  local times; one calendar record (CAL-A4) carries an explicit UTC marker
  (`2025-03-13T19:00:00Z`). All timestamps are converted into a single
  organisational timezone (`America/New_York`) so they are comparable. Without
  this, CAL-A4 would look five hours away from its CRM counterpart and the match
  would be missed.
- **Emails.** One calendar attendee is stored as `raj.patel[at]atlasvc.com`
  (CAL-A16). It is repaired to `raj.patel@atlasvc.com`.
- **Missing values** are kept as `None` and recorded as quality flags — never
  invented and never used to drop a record.

Every repair appends a human-readable entry to the record's `quality_flags`, which
is exposed through the API and shown in the UI.

### 2. Match with a score, not a key

There is no shared identifier between the sources, so reconciliation is a scored
problem. A candidate pair must fall on the **same calendar day** (hard gate), then
accumulates evidence:

| Signal | Weight | Rationale |
|---|---|---|
| Time within 30 min | 0.40 | Strongest scheduling evidence |
| Time within 3 h | 0.40 × decay | Partial credit, decaying with distance |
| CRM time missing | 0.15 neutral | Time is unusable as evidence either way |
| Attendee/client name overlap | up to 0.30 | Calendar `first.last@domain` vs CRM client name |
| Title token overlap | up to 0.20 | Fuzzy, stopword-filtered |
| Location token overlap | up to 0.10 | Weakest, since locations are formatted differently |

A pair is accepted at a combined score of **≥ 0.55**. Matching is **greedy by
descending score** rather than first-come-first-served, so a strong pair always
wins over a weak one that happened to be processed earlier.

The accepted match stores its confidence and the list of contributing signals, so
the UI can explain *why* two records were joined.

### 3. Merge with provenance, surface conflicts

When records are merged, each field keeps **every source's candidate value**. A
field is marked as a conflict only when two sources supply genuinely different
values. The displayed value follows a fixed precedence rule:

- **CRM wins** for relationship data: title, location, meeting type, status,
  client, owner.
- **Calendar wins** for scheduling data: start/end time, organizer, attendees.

The losing value is never discarded — it remains visible in the UI, struck through
beneath the chosen value.

---

## Key decisions and trade-offs

| # | Situation | Decision | Why |
|---|---|---|---|
| 1 | No common ID between sources | Scored, explainable matching | A single key cannot work; a score can be tuned and audited |
| 2 | `CAL-A4` is UTC, CRM is naive | Convert everything to `America/New_York` | Otherwise the pair looks 5 h apart and is missed |
| 3 | `CRM-1008` malformed date | Repair + log a quality flag | Recoverable data should be recovered, but visibly |
| 4 | `CAL-A16` `[at]` email | Repair + log | Same reasoning; it is unambiguous |
| 5 | `CRM-1002` "In-Person" vs `CAL-A2` "Zoom" | Report as a conflict, choose CRM's value | Real disagreements are the point of the exercise |
| 6 | CRM notes vs calendar description | **Not** a conflict | Different artefacts; flagging them would bury real conflicts (this fix alone removed 32 false conflicts: 59 → 27) |
| 7 | Owner name vs owner email | **Not** a conflict | Same fact, different representation |
| 8 | `CAL-A5` / `CAL-A6` near-duplicate | Flag, never auto-merge | 30 min apart with shared attendees could be a duplicate *or* a rescheduled meeting; a human should decide |
| 9 | `CAL-A3` / `CAL-A18` recurring series | Keep as separate meetings | They are distinct instances on different dates, not duplicates |
| 10 | Records present in only one source | Keep them, tagged `single_source` | The requirement is a *unified* view, not an intersection |
| 11 | `CRM-1007` has no meeting time | Neutral time credit + name/title evidence | Missing data should not veto an otherwise strong match |
| 12 | Storage | In-memory dict | 42 records, static file input; a database would add ceremony without benefit |
| 13 | Frontend | Vanilla HTML/CSS/JS | No build step keeps "one command to start" true; visual design is explicitly not evaluated |

### A note on decision 8

The duplicate detector requires **both** a shared attendee **and** a shared title
token, not merely proximity. An earlier version used proximity alone and produced a
false positive: `CAL-A7` ("Internal Pipeline Review", 16:00) and `CAL-A11` ("Client
Reception", 18:00) are two unrelated meetings on the same day. Requiring two
independent signals removed the false positive while still catching the genuine
`CAL-A5`/`CAL-A6` pair.

---

## Results on the provided data

42 source records (20 CRM + 22 Calendar) reconcile into **25 meetings**:

| Outcome | Count |
|---|---|
| Matched across both sources | 17 |
| CRM only | 3 |
| Calendar only | 5 |
| Meetings containing conflicts | 17 |
| Total conflicting fields | 30 |
| Possible same-source duplicates | 2 (one pair) |
| Records with data-quality flags | 25 |

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/meetings` | Unified list. Filters: `source`, `conflicts_only`, `date_from`, `date_to` |
| `GET` | `/api/meetings/{id}` | One meeting with full provenance and conflicts |
| `GET` | `/api/conflicts` | All conflicting fields, flattened |
| `GET` | `/api/stats` | Summary counts |
| `POST` | `/api/reingest` | Re-run ingestion and reconciliation |
| `GET` | `/health` | Liveness probe |

### Example

```bash
curl "http://127.0.0.1:8000/api/meetings?conflicts_only=true"
```

```jsonc
{
  "id": "meeting-CRM-1002-CAL-A2",
  "title": "Portfolio Walkthrough",
  "sources": ["calendar", "crm"],
  "source_ids": { "crm": "CRM-1002", "calendar": "CAL-A2" },
  "match_confidence": 0.8,
  "match_reasons": ["same_day", "time_within_30min", "attendee_name_overlap", "title_overlap"],
  "fields": {
    "location": {
      "value": "NYC Office - 30th Floor",   // chosen by precedence
      "conflict": true,
      "candidates": [
        { "source": "crm",      "source_id": "CRM-1002", "value": "NYC Office - 30th Floor" },
        { "source": "calendar", "source_id": "CAL-A2",   "value": "Zoom - https://zoom.us/j/98765432100" }
      ]
    }
  },
  "data_quality_flags": ["converted_to_org_timezone:created_at='2025-02-28T15:45:00Z'"]
}
```

---

## Frontend

A single page (no framework, no build step):

- **Stats bar** — totals for matches, one-sided records, conflicts, duplicates.
- **Table** — every unified meeting with source badges, match confidence, and
  conflict/duplicate/data-quality indicators.
- **Filters** — by source, conflicts only, and date range.
- **Detail panel** — clicking a row opens the full record: each field with its
  chosen value, every source's candidate value, conflicts highlighted, and the
  match signals that produced the join.

---

## Testing

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

31 tests, focused on the edge cases the dataset was built to exercise rather than
on coverage percentage:

- malformed CRM date repair
- the UTC-vs-naive timezone trap
- malformed email repair
- matching when the CRM time is missing
- one-sided records are retained
- recurring instances are not merged
- same-source duplicate is flagged, not merged
- unrelated same-day records are *not* flagged as duplicates
- conflict detection and precedence
- expected match/conflict counts (regression guard on the whole pipeline)
- API filters, error codes, and idempotent re-ingestion

### Test results

`31 passed, 0 failed`, verified at commit `15f89b6`. Reconciliation was also
cross-validated with an independent second implementation of the matcher:
17/17 matches agreed, zero false positives, 42/42 source records covered.
Full evidence — test inventory, oracle methodology, API smoke-test matrix, and
browser checks — is in
[`docs/testing-results.md`](docs/testing-results.md).

---

## Known limitations / what I'd do next

- **Matching is tuned to this dataset.** The weights and the 0.55 threshold were
  chosen against these 42 records. At scale I would learn the weights from labelled
  pairs rather than hand-tuning, and replace the greedy assignment with a proper
  optimal assignment (Hungarian algorithm) or a clustering pass.
- **Timezone assumption.** Naive CRM times are assumed to be `America/New_York`.
  A real system would carry an explicit timezone per record.
- **Duplicate detection is pairwise and conservative.** It will not catch
  duplicates more than 2 hours apart. A production system would need a
  blocking/clustering approach.
- **No persistence.** State is rebuilt at startup. Fine for a static file input;
  a real ingestion pipeline would need incremental sync, change detection, and a
  database.
- **The precedence rules are opinionated.** "CRM wins for location" is a product
  decision, not a technical one. It is centralised in one dict
  (`PRECEDENCE` in `app/reconcile.py`) so it is easy to change or make
  configurable.
- **No authentication or pagination.** Out of scope for this assessment, but
  required before this could be exposed beyond localhost.

---

## AI collaboration

This project was built with AI assistance. The planning, the reasoning behind each
decision, and the prompts used are documented in
[`docs/ai-collaboration.md`](docs/ai-collaboration.md).

---

## Time spent

Roughly **1 hour 30 minutes**, broken down as:

| Phase | Time |
|---|---|
| Analysing the dataset to find every trap | ~10 min |
| Models, ingestion, normalization | ~15 min |
| Reconciliation engine (matching, conflicts, duplicates) | ~25 min |
| REST API | ~10 min |
| Frontend | ~10 min |
| Tests | ~10 min |
| README and AI-collaboration docs | ~10 min |

The largest single time sink was the reconciliation engine, and specifically
tuning it against the data: the first version over-reported conflicts (59 instead
of 30) and produced a false duplicate, both of which required rethinking the
rules rather than tweaking constants.
