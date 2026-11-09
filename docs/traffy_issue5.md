# Issue #5: implementation audit and corrections

This follow-up is scoped to member 2's Checkpoint assignment:
<https://github.com/zero-h0ur/CS341-TuamYang/issues/5>.
Changes are consolidated on `feature/ingest-traffy` and reviewed with the
original Traffy implementation in PR #8. The former follow-up PR #9 is superseded.

## Hourly ingestion during heavy rain

The original handler defaulted to yesterday. Scheduling that unchanged handler
every hour would repeatedly skip yesterday and would not collect today's new
reports. Use the explicit hourly mode instead:

```powershell
python -X utf8 -m src.ingest_traffy --mode hourly --backend local
```

Lambda event: `{"mode":"hourly","storage_backend":"s3"}`.
It refreshes today's Bangkok partition even when a previous poll succeeded,
marks it `is_provisional=true`, and clips report times at the invocation cutoff.
During Bangkok's first hour it also refreshes yesterday so reports from the
last hour before midnight are not left behind. The normal daily schedule still
refreshes yesterday with `force=true` at 01:15 Bangkok time.

The source accepts dates only. Each poll fetches the relevant UTC dates, filters
the exact Bangkok window, and replaces the canonical partition without adding
duplicate ticket IDs. Changing source totals are tolerated only for provisional
current-day polling and recorded as warnings. Before 07:00 Bangkok, yesterday's
padded query can also touch a still-live UTC date: this case records a separate
warning and still excludes reports outside yesterday. Fully historical queries
keep the original strict consistency check. Newly arriving reports during pagination
may appear on the next poll; a live poll is not a transactional snapshot.

To install both schedules on the team's actual AWS environment:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File infrastructure/deploy_traffy.ps1 -BucketName "cs341-tuamyang-data" -Region "us-east-1" -EnableDailySchedule -EnableHourlySchedule
```

The hourly rule uses `cron(0 * * * ? *)` and the daily rule uses minute 15, so
their scheduled runs do not coincide. Do not run concurrent manual writers for
the same day. After the heavy-rain period, disable the optional hourly rule:

```powershell
aws events disable-rule --name tuamyang-traffy-hourly --region us-east-1
```

Actual deployment is still unverified. `tests/test_deploy_traffy.ps1` exercises
AWS CLI arguments with a mock function; it performs no cloud calls.
The PowerShell command scopes the execution policy to that process; it does
not change the user's machine-wide policy.

## Historical backfill from mid-2022

The CLI now defaults to **2022-06-01** through yesterday (inclusive Bangkok
dates), which makes the issue's history start explicit. Override `--start` if
the team selects a different interpretation of mid-year or a shorter period.
For a bounded run, use a new-date budget:

```powershell
python -X utf8 -m src.backfill_traffy --max-days 7 --backend local
```

Run the same command again to skip verified completed dates and process the
next seven dates. A skip does not consume the budget. `backfill_progress` states
whether the requested range is complete, whether the budget was exhausted and
which date comes next. The first failed date is the retry point.
Without `--max-days`, the CLI attempts the whole requested range; do not confuse
a successful bounded run with a completed multiyear download.
`--force` explicitly refreshes dates, so repeat it with the returned `next_date`
as `--start` to advance rather than refreshing the same first chunk again.

The initial historical API check was bounded to a real day in June 2022.
This verifies the old schema and access path without claiming that every day
since 2022 has been downloaded or is available from the source.

Observed result: **794 reports on 2022-06-01 Bangkok time**. The padded UTC
query returned 1,468 rows against an advertised total of 1,558, recorded as a
source coverage warning. See `evidence/traffy/issue5/historical_smoke.json`.

The full Local delivery is now verified for **2022-06-01 through 2026-11-09**:
**1,623 Bangkok days, 1,513,782 reports, zero missing/invalid daily partitions**.
The frozen first run covered 1,622 days through November 8. After midnight,
November 9 was ingested as a closed day and the entire delivery was audited again.
`evidence/traffy/full_backfill/summary.json` records `range_complete=true`, both
output hashes/counts per date and raw provenance checks. The final audit used no
network. See `docs/traffy_full_backfill.md` to reproduce it. AWS configuration
remains explicitly deferred by the owner after STS reported no credentials.

## Precision over the full set of predicted positives

Issue #5 asks for precision, which is a different metric from 94/100 overall
agreement. The original report's resolved-only precision removed uncertain
reference labels, making its 100% easy to misread.

The unchanged 100-report sample contains:

- 78 positive predictions: 76 reference-confirmed positive, 0 reference-negative,
  and 2 reference-uncertain predictions.
- A single precision point is **unresolved**. Keeping all 78 in the denominator
  gives bounds of **76/78 = 97.44%** to **78/78 = 100%**.
- These references are provisional AI text labels. Bounds describe agreement
  with that reference, not verified physical flooding or human accuracy.
- The legacy resolved-only score remains available for compatibility, clearly
  marked conditional and no longer used as the headline precision.

No source descriptions, sample IDs or reference labels were changed to improve
the result. Existing samples are now protected from replacement by a different
seed/count/snapshot. Human evaluation requires a nonempty reviewer name, valid
labels/rationales for every sampled ID and explicit `status: "completed"`.
The existing human template is left untouched and pending.

The owner confirmed human review has not yet been performed. A local working
copy combines each frozen description with its blank label/rationale fields at
`data/review/traffy/human_review_100_with_text.json`; see `docs/traffy_review_100.md`.
Real-data checks also verified all 9,614 case-study records have empty categories:
text rules produce 7,021 flood, 2,538 non-flood and 55 uncertain classifications.
Every UTC/Bangkok instant, epoch, Bangkok date and hour matched the source.
These behavior/time checks are recorded in
`evidence/traffy/real_text_time_checks.json`; they do not establish human precision.
Regenerate them with `python -X utf8 -m scripts.check_traffy_evidence`.

```powershell
python -X utf8 -m scripts.review_traffy evaluate --labels evidence/traffy/ai_review_labels.json
# After personally checking the unchanged full-text sample:
python -X utf8 -m scripts.review_traffy evaluate --labels data/review/traffy/human_review_100.json --output-dir evidence/traffy/human_review
```

## Issue checklist and remaining evidence

| Issue task | Implementation and observed verification | Remaining external evidence |
|---|---|---|
| Daily Lambda, hourly polling in heavy rain | Daily CLI/event preserved; explicit hourly mode, cutoff, midnight catch-up and optional rule added; live Local smoke test has 56 reports | Deploy/invoke Lambda and enable/observe the schedule on the team's AWS account |
| Backfill since mid-2022 | Full Local delivery verified for 2022-06-01 through 2026-11-09: 1,623 days, 1,513,782 reports, no daily gaps; reruns resume from verified outputs | Cloud upload/verification is deferred with AWS setup; preserve source coverage warnings |
| Filter flood text despite blank category | Existing v1.0 text rules retained; no category is required for affirmative text; negation and preventive descriptions have checks | Review limitations with the team; an isolated ambiguous `ท่วม` mention remains reviewable instead of being silently treated as proven flooding |
| UTC to Bangkok and 100-report precision | Time boundaries checked; the same seeded 100 reports retained; TP/FP/unresolved counts and full-denominator bounds reported | The owner reads the sample and marks the human review completed; unresolved positives must stay visible |
| PR evidence | Original implementation and Issue #5 corrections share `feature/ingest-traffy`; PR #8 merged into the ThaiWater branch. ThaiWater is now in `main` via PR #7, and Traffy has been integrated with that latest `main` for a direct PR to `main`; logical commits are preserved | AWS deployment and human-review evidence remain pending |

Local verification after integration with `main`: **81 Python checks pass** (65 Traffy and 16 ThaiWater),
the minimal Lambda ZIP imports from an isolated process, and the deployment
argument mock passes both schedule modes and a target failure scenario.
Recorded evidence is under `evidence/traffy/issue5/`; human labels and the
original September study data have not been altered.

```powershell
python -X utf8 -m scripts.test_traffy_checkpoint
powershell -NoProfile -ExecutionPolicy RemoteSigned -File tests/test_deploy_traffy.ps1
```
