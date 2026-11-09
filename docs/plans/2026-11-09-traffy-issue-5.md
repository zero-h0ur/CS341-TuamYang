# Close the implementation gaps against Traffy Issue #5

## Status
Implemented and verified locally on `fix/traffy-issue-5`, then consolidated into
`feature/ingest-traffy` at the user's request. PR #8 contains the complete work;
the former follow-up PR #9 is superseded. Logical commits are preserved.

## Summary
Review the existing Checkpoint work against the actual Issue #5. Add explicit
hourly live ingestion/scheduling, a bounded/resumable historical backfill from
mid-2022, and precision reporting that exposes unresolved review labels.
Commit each completed logical change separately, as requested by the user.

## Clarifying Questions
- The user explicitly authorized reviewing and fixing any gaps without another
  approval step and requested frequent commits.
- Use 2022-06-01 as the configurable mid-2022 history start, and verify an actual
  historical day rather than claiming the entire history has been downloaded.
- Hourly mode reads today's provisional Bangkok-day data. At the first Bangkok
  hour it also refreshes yesterday to cover the midnight boundary.
- Keep existing September source snapshots and review labels unchanged.
- Keep AI provisional labels distinct from human confirmation; unresolved labels
  must not disappear behind a headline precision of 100%.
- AWS deployment and human verification still require actual external evidence;
  do not fabricate either.

## File And Code References
- Issue: https://github.com/zero-h0ur/CS341-TuamYang/issues/5
- `src/ingest_traffy.py`: daily handler currently defaults to yesterday only.
- `src/traffy_client.py`: strict historical pagination needs a provisional mode.
- `src/backfill_traffy.py`: date range exists, but no history preset/bounded run.
- `infrastructure/deploy_traffy.ps1`: currently installs only a daily rule.
- `scripts/review_traffy.py`: currently excludes uncertain reference labels from
  precision, without reporting unresolved predicted positives in the headline.
- `tests/test_traffy.py`, `tests/test_traffy_delivery.py`: existing checks.

## Plan Todos
- [x] Add/test hourly Lambda/CLI mode, live cutoff, changing-total warnings,
  midnight catch-up, and opt-in hourly schedule; commit.
- [x] Add/test mid-2022 defaults and bounded resumed backfills; verify a real
  historical day and record scope; commit.
- [x] Correct precision reporting, counts/bounds and explicit human completion;
  rescore the existing frozen sample transparently; commit.
- [x] Update runbook/Issue checklist/PR description, run regression and delivery
  checks, and prepare the branch diff/PR body; commit verification separately.

### Recorded results
- Hourly Local API smoke: 56 current-day reports, provisional at the recorded cutoff.
- Historical Local API smoke: 794 reports on 2022-06-01, not a complete multiyear download.
- Frozen 100-report review: 76 TP, 0 FP, 2 unresolved predicted positives;
  precision is not yet a single resolved value (AI-reference bounds 97.44%–100%).
- 63 Python checks pass, including 11 ThaiWater regression checks.
- PowerShell AWS argument mock and isolated Lambda ZIP import pass.
- AWS deployment and human confirmation are explicitly pending.

## Grill-Me Outcome
Skipped: this is a bounded compatibility repair against explicit issue tasks,
using the existing architecture. Tests target the changed failure modes.

## Build From Plan
Preserve the existing daily CLI/event contract and shared ThaiWater modules.
Add behavior behind explicit hourly mode/flags. Use small historical verification
requests and a backfill budget instead of downloading years of data implicitly.
Make one commit after each validated logical change.

## Validation
Test hourly dispatch at normal hours and midnight, current-day cutoffs,
provisional pagination totals, closed-day failure behavior, bounded backfill
resume, precision denominator arithmetic, and pending human-review rejection.
Run the existing Traffy and ThaiWater regression checks, package/import the
Lambda, syntax-check PowerShell, and run the new deploy logic against mocked
AWS commands without touching cloud resources. Record actual API probe results.

## Risks
Live offsets are not a snapshot; newly arriving reports may be picked up on the
next poll, so mark current-day data provisional. API historical availability
must be observed. AI reference labels are not human ground truth. Separate
scheduled functions must not race as writers for the same date.

## Approval state
Implementation and frequent local commits are explicitly authorized by the
latest user request. Preserve responsible-AI and AWS verification limitations.
