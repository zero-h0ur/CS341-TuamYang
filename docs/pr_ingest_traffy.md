# Add daily/backfill Traffy ingestion and Checkpoint evidence

Traffy reports can now be ingested for a Bangkok calendar day or a historical
date range through CLI or `src.ingest_traffy.lambda_handler`. The pipeline saves
original API pages, publishes one deduplicated all-report partition per day,
and annotates flood/non-flood/uncertain descriptions with versioned reasons.
All-report counts are preserved for the Q2 denominator.

This branch depends on `feature/ingest-thaiwater` for shared Local/S3 storage.
Review against that branch to isolate member 2's changes; after the base enters
main, retarget the Traffy PR to main. If the base is squash-merged, first replay
only the Traffy commit onto main so the dependency's history is not duplicated.
No ThaiWater ingestion modules were changed.

## Validation and evidence

- Real public API ingestion for 2026-09-24 through 2026-09-26 Bangkok time:
  **9,614 unique reports**, with manifests, raw provenance and record hashes.
- Checkpoint replay: same counts/hashes, no new stored objects, no API calls.
- 46 local checks pass (35 Traffy checks plus 11 ThaiWater regression checks).
  They cover pagination, API failures, Thai timezone boundaries,
  deduplication, negation/prevention filtering, mocked Lambda/S3 wiring and
  honest review-metric handling.
- A minimal Lambda ZIP imports its handler in an isolated process.
- 100 reports selected uniformly with seed 341; AI read all descriptions without
  seeing predictions. Agreement is 94/100; six AI reference labels are uncertain.
  **These labels are provisional AI review, not human ground truth.** A human
  review template and scoring command are included.

## Source limitations and remaining verification

The public endpoint only accepted UTC date parameters during probing. It also
advertised totals larger than the rows it exposed (deltas 40/47/69); the client
fetches through an explicit empty page and records the discrepancy rather than
claiming full coverage of the advertised total. Report time is not confirmed
flood onset, and text/category matches do not prove a flood at a household.

**AWS deployment/invocation and live S3 writes have not been performed.** The
runbook includes packaging, Learner Lab deployment and optional daily scheduling.
Before claiming AWS or human-review completion, attach those actual results.
Source descriptions/photos/coordinates are excluded from committed evidence;
local raw snapshots remain available for inspection.

See `docs/traffy_checkpoint.md`, `reference/traffy_schema_contract.md` and
`evidence/traffy/` for reproducible commands, handoff rules and recorded results.
