# Testing & Verification Results

> **Verified against commit `15f89b6` (2026-10-07).**
> All numbers below were produced by actually running the suite and the
> verification scripts on that commit, not by hand-editing this file.

---

## 1. Summary

| Metric | Value |
|---|---|
| Automated tests | **31 passed**, 0 failed |
| Verification approach | Unit tests + independent oracle cross-validation + live API smoke tests + browser check |
| Reconciliation correctness | 17/17 matches confirmed against a hand-verified ground-truth table |
| Source record coverage | 42/42 records represented exactly once |
| README numeric claims | 8/8 verified against the live pipeline |
| Live API smoke checks | 12/12 passed |

---

## 2. Automated test suite

Run with:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Result:

```
======================== 31 passed, 1 warning in 17.48s ========================
```

(The warning is a `StarletteDeprecationWarning` from `httpx`'s test client —
informational only, no functional impact.)

### Test inventory

**`tests/test_reconcile.py` — pipeline edge cases (19 tests)**

| Trap guarded | Test |
|---|---|
| Malformed CRM date `"03-15/2025"` | `test_malformed_crm_date_is_repaired` |
| UTC-vs-naive timezone mismatch (CAL-A4) | `test_utc_calendar_time_matches_naive_crm_time`, `test_timezone_conversion_is_recorded` |
| Malformed email `[at]` | `test_malformed_email_is_repaired` |
| Match despite missing CRM time (CRM-1007) | `test_missing_time_does_not_prevent_match` |
| Ground-truth match count | `test_expected_match_count` |
| One-sided records retained | `test_one_sided_records_are_retained` |
| Recurring instances not merged | `test_recurring_instances_are_not_merged` |
| Same-source duplicate flagged, not merged | `test_same_source_duplicate_is_flagged_not_merged` |
| Unrelated same-day records not flag-dup'd | `test_unrelated_same_day_records_are_not_flagged_as_duplicates` |
| Conflict detection + precedence | `test_location_conflict_is_detected`, `test_crm_wins_location_precedence`, `test_expected_conflict_count` |
| Notes/description differences not conflicts | `test_notes_differences_are_not_conflicts` |
| Nothing silently dropped | `test_no_record_is_silently_dropped`, `test_quality_flags_present_for_dirty_records` |
| Null-field robustness | `test_normalize_crm_handles_null_fields`, `test_normalize_calendar_handles_empty_attendees` |
| Same-day hard gate | `test_same_day_is_a_hard_gate` |

**`tests/test_api.py` — HTTP surface (12 tests)**

`test_health`, `test_list_meetings`, `test_filter_by_source`,
`test_invalid_source_is_rejected`, `test_conflicts_only_filter`,
`test_date_range_filter`, `test_get_single_meeting`,
`test_unknown_meeting_returns_404`, `test_conflicts_endpoint`,
`test_stats_endpoint`, `test_reingest_is_idempotent`, `test_index_page_served`

---

## 3. Independent oracle cross-validation

To avoid confirming the pipeline *with itself*, a second matcher was written from
scratch: no app code imported, different logic (surname-in-attendee-email rules +
raw `datetime` arithmetic), operating directly on the JSON files.

| Check | Result |
|---|---|
| App matches == hand-verified ground-truth table | ✅ 17/17 |
| Pairs found by the oracle but not by the app (false positives) | ✅ 0 |
| Pairs found by the app but not by the oracle | 5 — all meetings with **no client name** (internal team meetings `CRM-1006`, `CRM-1009`, `CRM-1013`, `CRM-1019`, and `CRM-1017` "Multiple"). The oracle's name-based rule cannot match these by design; the app handles them via owner/title/location overlap. Each was verified individually: exact date+time, matching location, matching owner (for `CRM-1013`, the CRM owner appears in the calendar attendees list). |
| Record coverage | ✅ 42/42 source records, each represented in exactly one meeting |

**Notable find:** the first version of the oracle missed almost all matches, and
the failure mode reproduced precisely the timezone trap the app was built to
solve — naive timestamps were silently interpreted in the machine's local zone
(IST) instead of the organisation's. This is independent confirmation that
`app/ingest.py`'s org-local assumption for naive times is doing real work.

### Ground-truth match table

```
CRM-1001 ↔ CAL-A1    CRM-1002 ↔ CAL-A2    CRM-1004 ↔ CAL-A4
CRM-1005 ↔ CAL-A5    CRM-1006 ↔ CAL-A7    CRM-1007 ↔ CAL-A8
CRM-1008 ↔ CAL-A9    CRM-1009 ↔ CAL-A10   CRM-1011 ↔ CAL-A12
CRM-1012 ↔ CAL-A13   CRM-1013 ↔ CAL-A14   CRM-1014 ↔ CAL-A15
CRM-1015 ↔ CAL-A16   CRM-1016 ↔ CAL-A17   CRM-1017 ↔ CAL-A20
CRM-1018 ↔ CAL-A21   CRM-1019 ↔ CAL-A22
```

---

## 4. Live API smoke tests

Server started with `./run.sh` and exercised over HTTP (not via test client).

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | `GET /health` | 200 | ✅ |
| 2 | `GET /` (index HTML) | 200 | ✅ |
| 3 | `GET /static/app.js` | 200 | ✅ |
| 4 | `GET /docs` (OpenAPI) | 200 | ✅ |
| 5 | `GET /api/meetings` count | 25 | ✅ |
| 6 | `?source=calendar` count | 22 | ✅ |
| 7 | `?conflicts_only=true` count | 17 | ✅ |
| 8 | Unknown meeting id | 404 | ✅ |
| 9 | `?source=bogus` | 400 | ✅ |
| 10 | `POST /api/reingest` | 25 (idempotent) | ✅ |
| 11 | Conflict detail on `meeting-CRM-1002-CAL-A2` | `location.conflict == true` | ✅ |
| 12 | `GET /api/stats` summary | 25 / 17 / 30 | ✅ |

---

## 5. Browser verification

Verified in a real browser against the running server:

- 25 meeting rows render with correct stats bar (25 · 17 · 3 · 5 · 17 · 30 · 2 · 25).
- "Conflicts only" filter narrows the table to exactly 17 rows.
- Detail panel shows per-field provenance: chosen value, every source's
  candidate value, conflicts highlighted, match signals listed.
- Times render in the viewer's local timezone (expected browser behaviour;
  the API always serves `America/New_York`-normalised ISO timestamps).

---

## 6. Known gaps (honesty section)

- **Threshold tuning.** The match threshold (0.55) and one neutral credit value
  were tuned against this dataset's known answers. The *rules* are principled;
  the *constants* are fitted. See the README's limitations section.
- **No fuzz/property testing.** Inputs beyond the provided files (e.g. multiple
  simultaneous malformed fields on one record) have not been exhaustively
  tested, though null-field robustness is covered.
- **One transient observation:** during smoke testing, curl fired before
  uvicorn had finished binding and got connection-refused. This is a
  test-harness race (the server was up moments later); `run.sh` itself waits
  correctly. Not reproduced since.
