# Issue #5: implementation audit and corrections

This follow-up is scoped to member 2's Checkpoint assignment:
<https://github.com/zero-h0ur/CS341-TuamYang/issues/5>.
Changes live on `fix/traffy-issue-5`, based on the original Traffy branch.

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
.\infrastructure\deploy_traffy.ps1 -BucketName "cs341-tuamyang-data" -Region "us-east-1" -EnableDailySchedule -EnableHourlySchedule
```

The hourly rule uses `cron(0 * * * ? *)` and the daily rule uses minute 15, so
their scheduled runs do not coincide. Do not run concurrent manual writers for
the same day. After the heavy-rain period, disable the optional hourly rule:

```powershell
aws events disable-rule --name tuamyang-traffy-hourly --region us-east-1
```

Actual deployment is still unverified. `tests/test_deploy_traffy.ps1` exercises
AWS CLI arguments with a mock function; it performs no cloud calls.

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

The historical API check is deliberately bounded to a real day in June 2022.
This verifies the old schema and access path without claiming that every day
since 2022 has been downloaded or is available from the source.

Observed result: **794 reports on 2022-06-01 Bangkok time**. The padded UTC
query returned 1,468 rows against an advertised total of 1,558, recorded as a
source coverage warning. See `evidence/traffy/issue5/historical_smoke.json`.
