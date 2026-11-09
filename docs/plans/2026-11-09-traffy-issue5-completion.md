# Complete the remaining Traffy Issue #5 work

## Status
In progress. The user explicitly requested completion of Issue #5, then deferred
AWS configuration. Continue the same issue on `feature/ingest-traffy`, preserving
the user's request for one feature branch and one PR.

## Summary
Download and verify the full Bangkok-day history from 2022-06-01 through
2026-11-08 (the frozen yesterday cutoff when this run started). Preserve the
September review snapshot, existing raw provenance, and honest review labels.

## Clarifying Questions
AWS STS returned NoCredentials. The user answered that AWS configuration should
be done later, so cloud deployment is deferred by the user, not claimed complete.
The existing 100-report evaluation is AI provisional; human labels remain pending.

## File And Code References
- `src/backfill_traffy.py`: existing resumable sequential ingestion.
- `src/ingest_traffy.py`: daily ownership, checkpoints, hashes and publication.
- `src/traffy_client.py`: actual API pages, retry and pagination validation.
- `scripts/run_traffy_backfill.py`: bounded workers, global request pacing,
  durable progress, retries and full-range verification.
- `tests/test_traffy_full_backfill.py`: operational failure and integrity checks.
- `docs/traffy_issue5.md`, `docs/pr_ingest_traffy.md`: actual completion evidence.

## Plan Todos
- [x] Read the live Issue #5 and inspect AWS credentials without exposing secrets.
- [x] Start real full-range Local ingestion using existing checkpoints.
- [x] Add a controlled runner with at most four distinct-date writers and a
  shared API request limiter. Stop the sequential run before switching runners.
- [ ] Verify every expected date, both output hashes, counts and raw references.
- [ ] Record actual progress/completion and update the existing PR.
- [ ] Deploy and invoke Lambda/S3 and observe schedules after AWS configuration
  is supplied. Deferred by the user's explicit instruction.
- [ ] Score human labels if/when supplied; never invent a human review.

## Build From Plan
Keep source payloads in ignored Local storage. Continue from verified checkpoints.
Retry failed days a bounded number of times and report gaps explicitly. Limit
request starts globally and do not run two writers for the same date. Write
progress atomically; provide a stop file to finish in-flight days gracefully.
Keep operational evidence free of full report text or credentials.

## Validation
The expanded offline suite passed 73/73 checks, including the ten new history
audit, damaged-output, retry, date ownership, pacing and graceful-stop checks.
Test missing dates, damaged outputs, retries, concurrency bounds and graceful
stopping. Run the existing Traffy/ThaiWater suite after implementation changes.
Audit all expected days before reporting range_complete=true.

## Risks
Historical API totals exceed exposed rows; preserve coverage warnings. Source
changes and transient failures can require retries. Disk space and elapsed time
must be observed. An active background job is not a completed download.

## Approval state
Execution, necessary fixes, commits and pushes are authorized by the user's
request. AWS configuration is explicitly deferred. Do not merge the PR or claim
AWS/human verification without actual evidence.
