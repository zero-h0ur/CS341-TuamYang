# Address Traffy Issue #5: hourly ingestion, historical backfill and precision

The original daily handler selected yesterday, so running it hourly would keep
skipping yesterday instead of ingesting today's reports. Add explicit hourly
mode with a live cutoff, provisional flags, changing-total warnings and a
midnight catch-up. Supply an opt-in hourly EventBridge rule and check both daily
and hourly deployment arguments with mocked AWS calls.

Backfill now defaults to 2022-06-01 through yesterday. A `--max-days` budget
limits new processed dates without charging verified checkpoint skips; progress
reports the next date and whether the requested range is really complete.

Precision now keeps unresolved predicted positives visible: the unchanged
100-report AI review has TP=76, FP=0 and 2 unresolved positives out of 78 positive
predictions. Its point precision is unresolved, with bounds 97.44%–100% against
the provisional AI reference. The old conditional resolved-only metric is
retained but not used as a headline. Existing review work cannot be overwritten
with different sampling parameters, and human scoring requires explicit
completion.

## Validation

- Real current-day hourly Local ingestion: 56 reports on 2026-11-09 at the
  recorded cutoff; no actual AWS schedule was invoked.
- Real historical Local backfill: 794 reports on 2022-06-01. This is a bounded
  schema/access check, not a claim that every year has been downloaded.
- Tests cover live refresh, midnight boundaries, changing totals, resumed
  bounded backfill, precision denominators/bounds and pending human labels.
- All 63 local Python checks pass (52 Traffy and 11 ThaiWater); the Lambda ZIP
  imports in isolation.
- PowerShell deployment checks use an AWS mock, including target failure.

This branch is based on `feature/ingest-traffy` / PR #8, so review against that
branch to isolate the fixes. Commits are split by hourly ingestion, backfill,
precision and final verification as requested.

Refs #5. AWS deployment/live S3 verification and the user's human confirmation
of the 100 reports remain pending; this PR does not close those evidence gaps.
