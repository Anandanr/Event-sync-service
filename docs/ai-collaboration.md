# AI Collaboration Log

The assessment explicitly encourages AI assistance and asks for transparency about
how it was used. This document records the process: what I asked, what the AI
produced, what I changed, and where I disagreed with it.

**Tooling:** GitHub Copilot in VS Code (agent mode), working directly in the repo.

---

## 1. Starting point: understanding the problem, not writing code

The first prompt was deliberately *not* "build this". It was:

> "give me a plan and recommendation for this project"

Before any code was written, the AI was asked to read the problem statement and
both data files and produce an analysis. The useful part of that response was not
the plan — it was the insistence on **reading every record first**. The problem
statement says "We have intentionally not told you how to handle any of these
cases", which means the traps *are* the assessment. Guessing at them would have
produced a generic-looking solution.

### What the analysis found

A scripted pass over both files (rather than eyeballing them) surfaced the full set
of anomalies:

| Anomaly | Record(s) | Category |
|---|---|---|
| Malformed date `"03-15/2025"` | CRM-1008 | Normalization |
| UTC timestamp `19:00:00Z` | CAL-A4 | Timezone |
| Obfuscated email `raj.patel[at]...` | CAL-A16 | Normalization |
| Null location | CRM-1003, 1007, 1014, 1018 | Missing data |
| Null client name/company | CRM-1006, 1009, 1013, 1019 | Missing data |
| Null meeting time | CRM-1007 | Missing data |
| Empty notes | CRM-1010 | Missing data |
| Empty attendees / null description | CAL-A11 | Missing data |
| Near-duplicate pair | CAL-A5 / CAL-A6 | Duplication |
| Recurring instances | CAL-A3 / CAL-A18 | Duplication |
| Conflicting locations | CRM-1002 vs CAL-A2, and 7 more | Conflict |

This table became the checklist the implementation was built against, and it is
the reason the README has a decisions table with one row per trap.

---

## 2. The key insight the AI contributed

The single most valuable observation was about **CAL-A4**:

> CAL-A4 is `2025-03-13T19:00:00Z`. CRM-1004 is `2025-03-13` at `14:00`. If you
> compare a UTC timestamp against a naive local time, they look five hours apart
> and you miss the match entirely.

This is a genuinely subtle trap. A naive implementation would either miss the
match or "fix" it by widening the time window, which would then cause false
positives elsewhere. The correct fix is to normalize *all* timestamps into one
timezone before matching.

I verified the arithmetic independently (DST began 2025-03-09, so 2025-03-13 is
EDT, UTC-4, making 19:00Z = 15:00 local — one hour from the CRM record, not five).
The AI's framing was right; the exact offset needed checking, which is a good
illustration of why verification matters even when the reasoning is sound.

---

## 3. Where I overrode the AI

### 3.1 Over-reporting conflicts

The first working version reported **59 conflicts**. Inspecting the output showed
that most were noise: a CRM note and a calendar description are different
artefacts, and "Sarah Chen" vs `sarah.chen@firma.com` is the same fact written two
ways. Neither is a *conflict* — a conflict is two sources disagreeing about the
same fact.

I introduced a `NON_CONFLICTING_FIELDS` set (`notes`, `organizer`, `attendees`) and
the count dropped to 30, all of which are genuine (locations, titles, statuses, and
two start times). This was my decision, informed by looking at the actual output
rather than trusting the first result.

### 3.2 A false duplicate

The initial duplicate detector flagged any two same-source records within two hours
of each other. That produced a false positive: `CAL-A7` ("Internal Pipeline
Review", 16:00) and `CAL-A11` ("Client Reception", 18:00) are two unrelated
meetings on the same day.

I tightened the rule to require **both** a shared attendee **and** a shared title
token. This keeps the genuine `CAL-A5`/`CAL-A6` pair (same attendees, same client,
30 min apart) while dropping the false positive.

### 3.3 A threshold that was slightly too aggressive

`CRM-1007` (Meridian follow-up, no time) scored 0.533 against `CAL-A8` — just
below the 0.55 threshold. The signals were all correct (same day, matching client
name, matching title); the record simply lacked a time. I raised the neutral
credit for a missing CRM time from 0.10 to 0.15, which pushed it to 0.583.

I want to be explicit that this is **threshold tuning against a known answer**,
which is a real methodological weakness. The honest framing is that the *rule* is
principled (missing data should not veto an otherwise strong match) but the
*specific constant* is fitted to this dataset. The README's limitations section
says so.

### 3.4 Stack choice

The AI's stack recommendation (Python + FastAPI + SQLite + vanilla JS) was
reasonable, but I dropped SQLite. With 42 static records read once at startup, an
in-memory dict is simpler and removes a moving part. The store module is written
behind a small interface so swapping in a database later would not touch the
reconciliation logic.

---

## 4. Division of labour

**AI did most of:** the initial data analysis, the module scaffolding, the
boilerplate (Pydantic models, FastAPI wiring, CSS), and the first draft of the
matching heuristics.

**I did:** the conflict/duplicate rule corrections above, verification of every
count against the raw data, the test suite (written to pin down the specific traps
rather than to hit a coverage number), and all of the documentation.

**Verification loop:** after each change to the reconciliation rules, I re-ran the
pipeline and printed the full match table and conflict list, then checked each
result by hand against the source records. The 17 matches were confirmed
individually — no false positives, no missed pairs. This is what caught the
over-reporting and the false duplicate, neither of which would have been visible
from a passing test suite alone.

---

## 5. Honest assessment of the AI's contribution

**Where it helped most:** reading 42 records and extracting structure from them.
Doing that by hand would have been slower and more error-prone, and the CAL-A4
timezone trap in particular is easy to miss when skimming.

**Where it needed supervision:** anything involving thresholds or "how strict
should this rule be". The AI's first instinct was consistently too permissive on
conflicts and too loose on duplicates. Those are judgment calls about what the
*output* should look like, and they required looking at real results and deciding
what was actually wrong.

**What I would not delegate:** the decision of what counts as a conflict, the
decision not to auto-merge the duplicate pair, and the choice to keep one-sided
records. These are product decisions, and the README is where they are defended.

---

## 6. Prompts used (abridged)

1. "give me a plan and recommendation for this project" — with both data files
   attached.
2. "ok to start" — after reviewing the plan.
3. Follow-ups during implementation were targeted: *"is this contain README and
   time spent"* (a check against the submission requirements), and iterative
   verification of pipeline output after each rule change.

The full conversation is available in the session history; this document
summarises the substantive decisions rather than reproducing it verbatim.
