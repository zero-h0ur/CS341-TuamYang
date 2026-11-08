# Add Traffy daily/hourly ingestion, historical backfill and Checkpoint evidence

Traffy reports can now be ingested daily, polled hourly during heavy rain, or
backfilled through CLI or `src.ingest_traffy.lambda_handler`. The pipeline saves
original API pages, publishes one deduplicated all-report partition per day,
and annotates flood/non-flood/uncertain descriptions with versioned reasons.
All-report counts are preserved for the Q2 denominator.

All member 2 changes, including the Issue #5 corrections, are consolidated on
`feature/ingest-traffy` in this PR. This branch depends on
`feature/ingest-thaiwater` for shared Local/S3 storage. Review against that branch
to isolate member 2's changes; after the base enters main, retarget this PR to
main. If the base is squash-merged, first replay only the Traffy commits onto
main so the dependency's history is not duplicated. No ThaiWater ingestion
modules were changed.

## Behavior

- Daily ingestion selects yesterday in Bangkok time; hourly mode refreshes
  today's provisional partition with a live cutoff and midnight catch-up.
  Optional EventBridge rules supply daily and hourly Lambda events.
- Backfill defaults to 2022-06-01 through yesterday. `--max-days` bounds new
  processed dates; verified checkpoints are skipped and progress gives the next
  date. A bounded run does not imply that the full date range was downloaded.
- Flood rules use descriptions even when problem categories are empty, retain
  negation/prevention context and flag uncertain matches for review.
- UTC timestamps become explicit UTC and Bangkok fields. Raw pages, hashes and
  a final manifest support deduplication, replay and downstream validation.
- Review sampling protects existing work. Human scoring requires completed
  labels, while unresolved predicted positives remain visible in precision.

## Validation and evidence

- Real public API ingestion for 2026-09-24 through 2026-09-26 Bangkok time:
  **9,614 unique reports**, with manifests, raw provenance and record hashes.
- Checkpoint replay: same counts/hashes, no new stored objects, no API calls.
- Real hourly Local ingestion: **56 reports on 2026-11-09** at the recorded
  cutoff. Real bounded historical backfill: **794 reports on 2022-06-01**.
- 63 local Python checks pass (52 Traffy checks plus 11 ThaiWater regression checks).
  They cover pagination, API failures, Thai timezone boundaries,
  deduplication, negation/prevention filtering, live refresh, midnight catch-up,
  resumed backfill, mocked Lambda/S3 wiring and review-metric handling.
- A minimal Lambda ZIP imports its handler in an isolated process.
- PowerShell deployment checks validate daily/hourly schedule arguments and
  target failure handling using mocked AWS calls.
- 100 reports selected uniformly with seed 341; AI read all descriptions without
  seeing predictions. Agreement is 94/100; six AI reference labels are uncertain.
  Of 78 positive predictions, TP=76, FP=0 and two references are unresolved.
  Point precision remains unresolved, with bounds **97.44%–100%** against this
  provisional AI reference. **These labels are not human ground truth.**
  A human review template and scoring command are included.

## Source limitations and remaining verification

The public endpoint only accepted UTC date parameters during probing. It also
advertised totals larger than the rows it exposed (deltas 40/47/69); the client
fetches through an explicit empty page and records the discrepancy rather than
claiming full coverage of the advertised total. Report time is not confirmed
flood onset, and text/category matches do not prove a flood at a household.

**AWS deployment/invocation and live S3 writes have not been performed.** The
runbook includes packaging, Learner Lab deployment and optional daily/hourly
scheduling. The complete historical range has not been downloaded. Before
claiming AWS or human-review completion, attach those actual results.
Source descriptions/photos/coordinates are excluded from committed evidence;
local raw snapshots remain available for inspection.

See `docs/traffy_checkpoint.md`, `docs/traffy_issue5.md`,
`reference/traffy_schema_contract.md` and `evidence/traffy/` for reproducible
commands, handoff rules and recorded results.

Refs #5. This PR includes the work previously reviewed separately in PR #9;
external AWS and human-review evidence remain pending.
