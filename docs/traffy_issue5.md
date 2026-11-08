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
