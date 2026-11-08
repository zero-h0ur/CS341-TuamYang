# Traffy data contract for the Checkpoint

## Scope and integration

Owner: member 2 / Ingestion Traffy. Data source: the public Bangkok search API
specified in the Proposal, not the authenticated Exchange API.

Input to spatial join and curated analysis is the **all-report** daily envelope
under `incoming/traffy/`. It contains flood, non-flood and uncertain reports.
Filtering never removes reports from this denominator. The separate flood-only
file is convenient for inspection; it cannot provide the denominator for Q2.

## Storage keys

Keys are relative to either the existing S3 bucket or the local `data/` root.

```text
raw/traffy/dt=YYYY-MM-DD/batch=tf-.../page-NNNNN.json
incoming/traffy/dt=YYYY-MM-DD/traffy_YYYY-MM-DD.json
staged/traffy/dt=YYYY-MM-DD/flood_reports.json
checkpoints/traffy/dt=YYYY-MM-DD/manifest.json
```

- `dt` always means a **Bangkok calendar day**. Raw pages also contain adjacent
  UTC-day records because the API only accepts dates; read normalized incoming
  partitions for exact Bangkok-day membership.
- Raw archives contain the original API response and request parameters per
  attempt, including the terminal empty page. They are deliberately append-only
  across different attempts. **Do not UNION all raw attempts as unique reports.**
- Each completed daily incoming partition is replaced at its canonical key.
  Replay with a valid checkpoint skips the API; `--force` refreshes that day in
  place. For unchanged source input, row counts and record hashes stay stable.
- The manifest is an ingestion checkpoint, not member 4's `_SUCCESS.json`.
  Member 4 remains responsible for downstream validation/publication.
- Run only one writer per date at a time. This Checkpoint implementation does
  not provide a distributed lock for simultaneous manual and scheduled runs.

## Envelope (all reports)

| Field | Meaning |
|---|---|
| `envelope_version` | `1.0` |
| `batch_id` | ingestion attempt ID |
| `data_type` | `traffy` |
| `is_mock` | `false` for real API ingestion; tests explicitly mark fixtures |
| `source_endpoint` | public Bangkok search URL |
| `ingested_at_utc`, `ingested_at_bkk` | timezone-aware ingestion times |
| `date_bkk` | daily membership boundary |
| `record_count`, `records_sha256` | count and SHA-256 of the sorted `data` list |
| `rule_version` | `traffy-flood-v1.0` |
| `flood_count`, `uncertain_count` | text-rule counts, not verified flood events |
| `manifest_key`, `raw_page_keys` | provenance pointers |
| `data` | array of normalized reports |

## Report fields

| Field | Type / interpretation |
|---|---|
| `ticket_id` | string; deduplication/join key |
| `description` | original reporter text; agency `note` is not used as a label |
| `address` | source text; spatial join should use coordinates |
| `longitude`, `latitude` | float or null; API `coords` order is **longitude, latitude** |
| `source_categories` | combined nonempty `type` and `problem_type_abdul` values |
| `reported_at_utc` | ISO time ending `Z` |
| `reported_at_bkk`, `timestamp_iso` | the same instant with `+07:00` |
| `timestamp_epoch` | Unix seconds; directly comparable to ThaiWater epochs |
| `timestamp_source` | unmodified source timestamp |
| `report_date_bkk`, `report_hour_bkk` | keys for hourly roll-up |
| `last_activity_utc` | source revision/activity time, not flood onset |
| `state` | issue resolution status; not used to erase historical reports |
| `quality_flags` | invalid/missing coordinates, assumed timezone, etc. |
| `flood_classification` | `flood`, `non_flood`, or `uncertain` |
| `is_flood_report` | `true`, `false`, or **null** respectively |
| `flood_reason`, `flood_matched_terms` | explainable text-rule output |
| `flood_suppressed_terms` | phrases suppressed for negation/prevention |
| `source_category_flood`, `needs_review` | supporting category and uncertainty |

Use `is_flood_report IS TRUE` for the numerator. Keep **all** reports for the
denominator and show the uncertain count separately. A missing coordinate does
not delete a report; it is flagged so member 3 can measure spatial join coverage.
If a required ID or timestamp is invalid, the entire daily batch fails with raw
evidence retained rather than silently dropping records.

The old public search endpoint observed here returns UTC timestamps such as
`2026-09-24 05:30:00+00`. The normalized Bangkok time is 12:30 that day.
Do not add seven hours to the epoch or to an already timezone-aware value.
Naive source timestamps are assumed UTC and flagged. The separate Exchange API
documents a different convention, so it is not a drop-in source replacement.

## Completion, warnings and downstream triggers

Read a partition only when its manifest is `SUCCESS` and its `batch_id` and
`records_sha256` match the envelope. An S3 event for `incoming/traffy/` may arrive
just before the final manifest write; retry/wait for the matching success state.
A failed refresh leaves old incoming data in place but marks the current
manifest `FAILED`, so it cannot be mistaken for the new successful batch.

The source's advertised `total` was higher than the exposed rows for all three
demo windows. Completion means **the public endpoint returned an explicit empty
page**, not that all real-world reports or the advertised total were recovered.
The manifest records `source_total_advisory`, `source_rows_fetched`,
`source_total_delta`, `source_exhausted` and `quality_warnings`. Preserve these
warnings when presenting coverage. The reason for the discrepancy is unknown.

If `total` changes between nonempty pages, a page repeats, the pagination limit
is exceeded, or the request fails after retries, no new incoming partition is
published. The Lambda raises on failure so the scheduler can retry.

## Analysis limits

Reports are a proxy for reported problems, not a full flood census. The timestamp
is when a report was submitted, not a verified flood start. Multiple tickets can
describe the same event. A requested drain operation may concern another area,
and a generic `น้ำท่วม` label does not prove flooding at the report coordinate.
Member 3 selects the study khwaeng/station mapping; member 5 performs Q1/Q2
analysis. This ingestion module does not make household evacuation decisions.
