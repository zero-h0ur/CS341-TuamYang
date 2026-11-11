-- ==============================================================================
-- CS341 TuamYang: Glue / Athena DDL for trusted.* and curated.khwaeng_hourly
-- Owner: Member 5 (nustapark6709650334)
-- Aligned with:
--   - Issue #1 / PR #12 (src/validation/pipeline.py JSONL schemas in trusted/)
--   - Issue #4, #6 / PR #7 (reference/schema_contract.md: 8 water + 14 rain stations,
--     composite key deduplication, usable_for_freeboard & usable_for_rainfall flags)
--   - Issue #5 / PR #10 (reference/traffy_schema_contract.md: full denominator)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Upstream Glue / Athena Tables over trusted/ JSONL Zone (from Issue #1)
-- ------------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS trusted;
CREATE DATABASE IF NOT EXISTS curated;

CREATE EXTERNAL TABLE IF NOT EXISTS trusted.waterlevel (
    api_station_id INT,
    tele_station_oldcode STRING,
    station_name_th STRING,
    latitude DOUBLE,
    longitude DOUBLE,
    waterlevel_datetime STRING COMMENT 'YYYY-MM-DD HH:MM in Asia/Bangkok (UTC+7)',
    timestamp_iso STRING,
    timestamp_epoch BIGINT,
    waterlevel_msl DOUBLE,
    min_bank_msl DOUBLE,
    diff_wl_bank_m DOUBLE,
    diff_source STRING,
    quality_flag STRING COMMENT 'NORMAL or SUSPECT_DATUM_MISMATCH',
    usable_for_freeboard BOOLEAN,
    validation_flags ARRAY<STRING>,
    batch_id STRING,
    ingested_at_utc STRING,
    validation_status STRING
)
PARTITIONED BY (dt STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://cs341-tuamyang-data/trusted/waterlevel/';

CREATE EXTERNAL TABLE IF NOT EXISTS trusted.rain (
    api_station_id INT,
    station_name_th STRING,
    latitude DOUBLE,
    longitude DOUBLE,
    rain_datetime STRING COMMENT 'YYYY-MM-DD HH:MM in Asia/Bangkok (UTC+7)',
    timestamp_epoch BIGINT,
    rain_mm DOUBLE,
    quality_flag STRING COMMENT 'OK or SUSPECT_RAIN_SENSOR_OUTAGE',
    usable_for_rainfall BOOLEAN,
    validation_flags ARRAY<STRING>,
    batch_id STRING,
    ingested_at_utc STRING,
    validation_status STRING
)
PARTITIONED BY (dt STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://cs341-tuamyang-data/trusted/rain/';

CREATE EXTERNAL TABLE IF NOT EXISTS trusted.traffy (
    ticket_id STRING,
    description STRING,
    address STRING,
    longitude DOUBLE,
    latitude DOUBLE,
    source_categories ARRAY<STRING>,
    reported_at_utc STRING,
    reported_at_bkk STRING,
    timestamp_iso STRING,
    timestamp_epoch BIGINT,
    timestamp_source STRING,
    report_date_bkk STRING,
    report_hour_bkk INT,
    last_activity_utc STRING,
    state STRING,
    quality_flags ARRAY<STRING>,
    flood_classification STRING COMMENT 'flood, non_flood, or uncertain',
    is_flood_report BOOLEAN,
    flood_rule_version STRING,
    flood_reason STRING,
    flood_matched_terms ARRAY<STRING>,
    flood_suppressed_terms ARRAY<STRING>,
    source_category_flood BOOLEAN,
    needs_review BOOLEAN,
    date_bkk STRING,
    is_provisional BOOLEAN,
    batch_id STRING,
    ingested_at_utc STRING,
    validation_status STRING
)
PARTITIONED BY (dt STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://cs341-tuamyang-data/trusted/traffy/';


-- ------------------------------------------------------------------------------
-- 2. Curated External Table DDL: curated.khwaeng_hourly
-- Granularity: 1 row per subdistrict (khwaeng) per Bangkok calendar hour
-- ------------------------------------------------------------------------------

CREATE EXTERNAL TABLE IF NOT EXISTS curated.khwaeng_hourly (
    hour INT COMMENT 'Bangkok calendar hour (0-23)',
    khwaeng STRING COMMENT 'Bangkok subdistrict name',
    khet STRING COMMENT 'Bangkok district name',
    center_latitude DOUBLE COMMENT 'Subdistrict representative latitude',
    center_longitude DOUBLE COMMENT 'Subdistrict representative longitude',
    paired_water_station_id INT COMMENT 'ID of nearest canal waterlevel telemetry station',
    water_station_name STRING COMMENT 'Name of paired water station',
    canal_name STRING COMMENT 'Name of canal/river',
    water_station_dist_km DOUBLE COMMENT 'Distance to paired water station (km)',
    avg_waterlevel_msl DOUBLE COMMENT 'Average canal water level in hour (m MSL)',
    max_waterlevel_msl DOUBLE COMMENT 'Peak canal water level in hour (m MSL)',
    min_bank_msl DOUBLE COMMENT 'Minimum bank height (m MSL)',
    raw_freeboard_m DOUBLE COMMENT 'Unfiltered min_bank - max_wl preserved for DQ audit',
    min_freeboard_m DOUBLE COMMENT 'Usable remaining bank height before overflow (NULL if usable_for_freeboard=false)',
    water_quality_flag STRING COMMENT 'NORMAL or SUSPECT_DATUM_MISMATCH',
    usable_for_freeboard BOOLEAN COMMENT 'False for Station 10 datum mismatch',
    is_overflow BOOLEAN COMMENT 'True if usable_for_freeboard and min_freeboard_m <= 0',
    risk_tier STRING COMMENT 'CRITICAL_OVERFLOW, HIGH_ALERT, WATCH, NORMAL, SUSPECT_DATUM, or NO_TELEMETRY',
    paired_rain_station_id INT COMMENT 'ID of nearest usable rain telemetry station',
    rain_station_name STRING COMMENT 'Name of paired rain station',
    rain_station_dist_km DOUBLE COMMENT 'Distance to paired rain station (km)',
    rain_mm DOUBLE COMMENT 'Hourly rainfall from sliding window series (mm)',
    rainfall_daily_mm DOUBLE COMMENT 'Daily rainfall from paired usable station (mm)',
    rain_quality_flag STRING COMMENT 'OK or SUSPECT_RAIN_SENSOR_OUTAGE',
    usable_for_rainfall BOOLEAN COMMENT 'False for Station 852087 rain sensor outage',
    total_reports INT COMMENT 'Total Traffy citizen complaints in khwaeng and hour (Denominator)',
    flood_reports INT COMMENT 'Complaints classified as flood (Numerator, is_flood_report IS TRUE)',
    uncertain_reports INT COMMENT 'Complaints with uncertain flood classification',
    non_flood_reports INT COMMENT 'Complaints classified as non-flood',
    flood_report_ratio DOUBLE COMMENT 'Ratio of flood complaints to total complaints'
)
COMMENT 'Curated data mart for Bangkok flood risk by subdistrict and hour'
PARTITIONED BY (
    dt STRING COMMENT 'Bangkok calendar date (YYYY-MM-DD)'
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://cs341-tuamyang-data/curated/khwaeng_hourly/';


-- ------------------------------------------------------------------------------
-- 3. Transformation Query (Athena / Trino / Presto SQL)
-- Implements:
--   * Composite key deduplication (api_station_id, timestamp_epoch) with latest-batch-wins
--   * Sliding-window hourly rain deduplication (latest-batch-wins per schema_contract 8.2)
--   * Datum mismatch isolation (usable_for_freeboard = false -> SUSPECT_DATUM)
--   * Outage rain sensor exclusion (usable_for_rainfall = true for nearest station)
--   * Full Traffy report denominator preservation
-- ------------------------------------------------------------------------------

/*
WITH waterlevel_dedup AS (
    SELECT
        api_station_id,
        waterlevel_datetime,
        timestamp_epoch,
        waterlevel_msl,
        min_bank_msl,
        diff_wl_bank_m,
        coalesce(quality_flag, 'NORMAL') AS quality_flag,
        coalesce(usable_for_freeboard, TRUE) AS usable_for_freeboard,
        row_number() OVER (
            PARTITION BY api_station_id, timestamp_epoch
            ORDER BY ingested_at_utc DESC, batch_id DESC
        ) AS rn
    FROM trusted.waterlevel
    WHERE waterlevel_msl IS NOT NULL
),
waterlevel_hourly AS (
    SELECT
        api_station_id,
        substr(waterlevel_datetime, 1, 10) AS dt,
        cast(substr(waterlevel_datetime, 12, 2) AS INT) AS hour,
        round(avg(waterlevel_msl), 3) AS avg_waterlevel_msl,
        round(max(waterlevel_msl), 3) AS max_waterlevel_msl,
        round(min(min_bank_msl), 3) AS min_bank_msl,
        round(coalesce(min(diff_wl_bank_m), min(min_bank_msl) - max(waterlevel_msl)), 3) AS raw_freeboard_m,
        CASE
            WHEN bool_and(usable_for_freeboard)
            THEN round(coalesce(min(diff_wl_bank_m), min(min_bank_msl) - max(waterlevel_msl)), 3)
            ELSE NULL
        END AS min_freeboard_m,
        arbitrary(quality_flag) AS water_quality_flag,
        bool_and(usable_for_freeboard) AS usable_for_freeboard,
        (bool_and(usable_for_freeboard) AND coalesce(min(diff_wl_bank_m), min(min_bank_msl) - max(waterlevel_msl)) <= 0.0) AS is_overflow
    FROM waterlevel_dedup
    WHERE rn = 1
    GROUP BY
        api_station_id,
        substr(waterlevel_datetime, 1, 10),
        cast(substr(waterlevel_datetime, 12, 2) AS INT)
),
rain_dedup AS (
    SELECT
        api_station_id,
        rain_datetime,
        timestamp_epoch,
        rain_mm,
        coalesce(quality_flag, 'OK') AS quality_flag,
        coalesce(usable_for_rainfall, TRUE) AS usable_for_rainfall,
        row_number() OVER (
            PARTITION BY api_station_id, timestamp_epoch
            ORDER BY ingested_at_utc DESC, batch_id DESC
        ) AS rn
    FROM trusted.rain
    WHERE rain_mm IS NOT NULL
      AND coalesce(usable_for_rainfall, TRUE) = TRUE
),
rain_hourly AS (
    SELECT
        api_station_id,
        substr(rain_datetime, 1, 10) AS dt,
        cast(substr(rain_datetime, 12, 2) AS INT) AS hour,
        round(sum(rain_mm), 2) AS rain_mm,
        round(sum(sum(rain_mm)) OVER (
            PARTITION BY api_station_id, substr(rain_datetime, 1, 10)
        ), 2) AS rainfall_daily_mm,
        arbitrary(quality_flag) AS rain_quality_flag,
        bool_and(usable_for_rainfall) AS usable_for_rainfall
    FROM rain_dedup
    WHERE rn = 1
    GROUP BY
        api_station_id,
        substr(rain_datetime, 1, 10),
        cast(substr(rain_datetime, 12, 2) AS INT)
),
traffy_dedup AS (
    SELECT
        ticket_id,
        report_date_bkk AS dt,
        report_hour_bkk AS hour,
        coalesce(nullif(regexp_extract(address, 'แขวง([^\s,]+)', 1), ''), 'ไม่ระบุ') AS khwaeng,
        coalesce(nullif(regexp_extract(address, 'เขต([^\s,]+)', 1), ''), 'ไม่ระบุ') AS khet,
        latitude,
        longitude,
        is_flood_report,
        flood_classification,
        row_number() OVER (
            PARTITION BY ticket_id
            ORDER BY ingested_at_utc DESC, batch_id DESC
        ) AS rn
    FROM trusted.traffy
),
traffy_hourly AS (
    SELECT
        dt,
        hour,
        khwaeng,
        khet,
        count(*) AS total_reports,
        count(CASE WHEN is_flood_report IS TRUE THEN 1 END) AS flood_reports,
        count(CASE WHEN flood_classification = 'uncertain' OR is_flood_report IS NULL THEN 1 END) AS uncertain_reports,
        count(CASE WHEN is_flood_report IS FALSE THEN 1 END) AS non_flood_reports,
        round(avg(latitude), 5) AS center_latitude,
        round(avg(longitude), 5) AS center_longitude
    FROM traffy_dedup
    WHERE rn = 1
    GROUP BY dt, hour, khwaeng, khet
)
SELECT
    t.hour,
    t.khwaeng,
    t.khet,
    t.center_latitude,
    t.center_longitude,
    ref.paired_water_station_id,
    w.station_name_th AS water_station_name,
    w.river_name_th AS canal_name,
    ref.water_station_dist_km,
    wh.avg_waterlevel_msl,
    wh.max_waterlevel_msl,
    coalesce(wh.min_bank_msl, w.min_bank_msl) AS min_bank_msl,
    wh.raw_freeboard_m,
    wh.min_freeboard_m,
    coalesce(wh.water_quality_flag, w.quality_flag, 'NORMAL') AS water_quality_flag,
    coalesce(wh.usable_for_freeboard, w.usable_for_freeboard, TRUE) AS usable_for_freeboard,
    coalesce(wh.is_overflow, FALSE) AS is_overflow,
    CASE
        WHEN coalesce(wh.usable_for_freeboard, w.usable_for_freeboard, TRUE) = FALSE THEN 'SUSPECT_DATUM'
        WHEN wh.min_freeboard_m <= 0.0 THEN 'CRITICAL_OVERFLOW'
        WHEN wh.min_freeboard_m <= 0.5 THEN 'HIGH_ALERT'
        WHEN wh.min_freeboard_m <= 1.0 THEN 'WATCH'
        WHEN wh.min_freeboard_m > 1.0 THEN 'NORMAL'
        ELSE 'NO_TELEMETRY'
    END AS risk_tier,
    ref.paired_rain_station_id,
    rn.station_name_th AS rain_station_name,
    ref.rain_station_dist_km,
    rh.rain_mm,
    rh.rainfall_daily_mm,
    coalesce(rh.rain_quality_flag, rn.quality_flag, 'OK') AS rain_quality_flag,
    coalesce(rh.usable_for_rainfall, rn.usable_for_rainfall, TRUE) AS usable_for_rainfall,
    t.total_reports,
    t.flood_reports,
    t.uncertain_reports,
    t.non_flood_reports,
    round(cast(t.flood_reports AS DOUBLE) / nullif(t.total_reports, 0), 4) AS flood_report_ratio,
    t.dt
FROM traffy_hourly t
LEFT JOIN reference.khwaeng_station_mapping ref
    ON t.khwaeng = ref.khwaeng AND t.khet = ref.khet
LEFT JOIN reference.stations_water w
    ON ref.paired_water_station_id = w.api_station_id
LEFT JOIN reference.stations_rain rn
    ON ref.paired_rain_station_id = rn.api_station_id
LEFT JOIN waterlevel_hourly wh
    ON ref.paired_water_station_id = wh.api_station_id
    AND t.dt = wh.dt
    AND t.hour = wh.hour
LEFT JOIN rain_hourly rh
    ON ref.paired_rain_station_id = rh.api_station_id
    AND t.dt = rh.dt
    AND t.hour = rh.hour;
*/
