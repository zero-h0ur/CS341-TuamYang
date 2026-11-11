-- ==============================================================================
-- CS341 TuamYang: Automated Data Quality Audit Queries
-- Table: curated.khwaeng_hourly
-- Owner: Member 5 (nustapark6709650334)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- DQ-1: Missing Telemetry & Data Completeness Audit
-- ตรวจสอบความครบถ้วนของข้อมูลระดับน้ำและข้อมูลฝน
-- ------------------------------------------------------------------------------
SELECT
    dt,
    count(*) AS total_rows,
    count(CASE WHEN avg_waterlevel_msl IS NULL THEN 1 END) AS missing_waterlevel_rows,
    round(cast(count(CASE WHEN avg_waterlevel_msl IS NULL THEN 1 END) AS DOUBLE) / count(*) * 100, 2) AS missing_waterlevel_pct,
    count(CASE WHEN rainfall_daily_mm IS NULL THEN 1 END) AS missing_rain_rows,
    round(cast(count(CASE WHEN rainfall_daily_mm IS NULL THEN 1 END) AS DOUBLE) / count(*) * 100, 2) AS missing_rain_pct
FROM curated.khwaeng_hourly
GROUP BY
    dt
ORDER BY
    dt;

-- ------------------------------------------------------------------------------
-- DQ-2: Telemetry Outliers & Vertical Datum Mismatch Detection
-- ตรวจจับค่าระดับน้ำและระยะพ้นตลิ่งที่ผิดปกติ (เช่น สถานีภาษีเจริญ ID 10)
-- ------------------------------------------------------------------------------
SELECT
    paired_water_station_id,
    water_station_name,
    water_quality_flag,
    usable_for_freeboard,
    dt,
    hour,
    khwaeng,
    avg_waterlevel_msl,
    min_bank_msl,
    raw_freeboard_m,
    min_freeboard_m,
    risk_tier,
    'OUTLIER: SUSPECT_DATUM_MISMATCH (raw_freeboard_m > 5.0m or avg_waterlevel_msl < -5.0m)' AS anomaly_reason
FROM curated.khwaeng_hourly
WHERE water_quality_flag = 'SUSPECT_DATUM_MISMATCH'
   OR usable_for_freeboard = FALSE
   OR raw_freeboard_m > 5.0
   OR avg_waterlevel_msl < -5.0
ORDER BY
    raw_freeboard_m DESC;

-- ------------------------------------------------------------------------------
-- DQ-3: Stale Station Latency & Resolution Audit
-- ตรวจสอบความถี่ของสถานี C.12 และสถานีโทรมาตรหลัก
-- ------------------------------------------------------------------------------
SELECT
    paired_water_station_id,
    water_station_name,
    water_quality_flag,
    usable_for_freeboard,
    count(DISTINCT dt) AS dates_count,
    count(DISTINCT CASE WHEN avg_waterlevel_msl IS NOT NULL THEN concat(dt, '-', cast(hour AS VARCHAR)) END) AS distinct_station_hours_reported,
    count(*) AS total_khwaeng_hour_records
FROM curated.khwaeng_hourly
GROUP BY
    paired_water_station_id,
    water_station_name,
    water_quality_flag,
    usable_for_freeboard
ORDER BY
    paired_water_station_id;

-- ------------------------------------------------------------------------------
-- DQ-4: Spatial Join & Coordinate Integrity Audit
-- ตรวจสอบ % ความสำเร็จในการสกัดชื่อแขวง-เขต และพิกัดจาก Traffy Fondue
-- ------------------------------------------------------------------------------
SELECT
    dt,
    sum(total_reports) AS total_reports,
    sum(CASE WHEN khwaeng != 'ไม่ระบุ' THEN total_reports ELSE 0 END) AS reports_with_valid_khwaeng,
    round(cast(sum(CASE WHEN khwaeng != 'ไม่ระบุ' THEN total_reports ELSE 0 END) AS DOUBLE) / sum(total_reports) * 100, 2) AS khwaeng_match_rate_pct,
    sum(CASE WHEN khet != 'ไม่ระบุ' THEN total_reports ELSE 0 END) AS reports_with_valid_khet,
    round(cast(sum(CASE WHEN khet != 'ไม่ระบุ' THEN total_reports ELSE 0 END) AS DOUBLE) / sum(total_reports) * 100, 2) AS khet_match_rate_pct
FROM curated.khwaeng_hourly
GROUP BY
    dt
ORDER BY
    dt;
