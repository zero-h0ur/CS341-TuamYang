-- ==============================================================================
-- CS341 TuamYang: Analytics Queries for Research Questions Q1 & Q2
-- Table: curated.khwaeng_hourly
-- Case Study Window: 2026-09-24 to 2026-09-26
-- Owner: Member 5 (nustapark6709650334)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Q1: ความสัมพันธ์ระหว่างฝนตกหนัก กับระดับน้ำและการล้นตลิ่งในคลอง
-- (Rainfall -> Canal Water Level & Overflow Dynamics)
-- ------------------------------------------------------------------------------

-- Q1.1: สรุปปริมาณฝน ระดับน้ำสูงสุด และระยะพ้นตลิ่งต่ำสุด รายวัน แยกตามสถานี
SELECT
    paired_water_station_id,
    water_station_name,
    canal_name,
    min_bank_msl,
    water_quality_flag,
    usable_for_freeboard,
    dt,
    max(rainfall_daily_mm) AS max_daily_rain_mm,
    round(avg(avg_waterlevel_msl), 3) AS daily_avg_waterlevel_msl,
    round(max(max_waterlevel_msl), 3) AS daily_max_waterlevel_msl,
    round(min(min_freeboard_m), 3) AS daily_min_freeboard_m,
    count(CASE WHEN is_overflow THEN 1 END) AS overflow_khwaeng_hours_count
FROM curated.khwaeng_hourly
WHERE dt IN ('2026-09-24', '2026-09-25', '2026-09-26')
    AND paired_water_station_id IS NOT NULL
GROUP BY
    paired_water_station_id,
    water_station_name,
    canal_name,
    min_bank_msl,
    water_quality_flag,
    usable_for_freeboard,
    dt
ORDER BY
    paired_water_station_id,
    dt;

-- Q1.2: ค้นหาชั่วโมงที่ระดับน้ำในคลองขึ้นแตะจุดสูงสุด (Peak Hour) ของแต่ละสถานีที่ใช้ประเมินตลิ่งได้
WITH ranked_hourly AS (
    SELECT
        paired_water_station_id,
        water_station_name,
        canal_name,
        dt,
        hour,
        max_waterlevel_msl,
        min_freeboard_m,
        is_overflow,
        row_number() OVER (
            PARTITION BY paired_water_station_id, dt
            ORDER BY max_waterlevel_msl DESC, hour ASC
        ) AS rnk
    FROM curated.khwaeng_hourly
    WHERE dt IN ('2026-09-24', '2026-09-25', '2026-09-26')
        AND max_waterlevel_msl IS NOT NULL
        AND usable_for_freeboard = TRUE
)
SELECT
    paired_water_station_id,
    water_station_name,
    canal_name,
    dt,
    hour AS peak_hour,
    max_waterlevel_msl AS peak_waterlevel_msl,
    min_freeboard_m,
    is_overflow
FROM ranked_hourly
WHERE rnk = 1
ORDER BY
    dt,
    peak_hour;


-- ------------------------------------------------------------------------------
-- Q2: ความสัมพันธ์ระหว่างระยะตลิ่งต่ำ (เสี่ยงล้น/ล้นตลิ่ง) กับรายงานน้ำท่วมในแขวง
-- (Low Freeboard -> Flood Complaints in Nearby Subdistricts)
-- ------------------------------------------------------------------------------

-- Q2.1: สรุปสัดส่วนรายงานน้ำท่วมจำแนกตามระดับความเสี่ยงของระยะพ้นตลิ่ง (Risk Tier)
SELECT
    risk_tier,
    CASE
        WHEN risk_tier = 'CRITICAL_OVERFLOW' THEN '<= 0.0 m (น้ำล้นตลิ่ง)'
        WHEN risk_tier = 'HIGH_ALERT' THEN '0.0 - 0.5 m (เฝ้าระวังสูงสุด)'
        WHEN risk_tier = 'WATCH' THEN '0.5 - 1.0 m (เฝ้าระวัง)'
        WHEN risk_tier = 'NORMAL' THEN '> 1.0 m (ปกติ)'
        WHEN risk_tier = 'SUSPECT_DATUM' THEN 'SUSPECT_DATUM_MISMATCH (สถานี 10 ภาษีเจริญ)'
        ELSE 'ไม่มีสัญญาณโทรมาตร'
    END AS freeboard_threshold_desc,
    count(*) AS khwaeng_hours_count,
    sum(total_reports) AS total_reports,
    sum(flood_reports) AS flood_reports,
    sum(uncertain_reports) AS uncertain_reports,
    sum(non_flood_reports) AS non_flood_reports,
    round(cast(sum(flood_reports) AS DOUBLE) / nullif(sum(total_reports), 0) * 100, 2) AS flood_report_ratio_pct
FROM curated.khwaeng_hourly
WHERE dt IN ('2026-09-24', '2026-09-25', '2026-09-26')
GROUP BY
    risk_tier
ORDER BY
    flood_report_ratio_pct DESC;

-- Q2.2: พัฒนาการรายวัน เปรียบเทียบสัดส่วนรายงานน้ำท่วมกับจำนวนชั่วโมงที่น้ำล้นตลิ่ง
SELECT
    dt,
    sum(total_reports) AS daily_total_reports,
    sum(flood_reports) AS daily_flood_reports,
    round(cast(sum(flood_reports) AS DOUBLE) / nullif(sum(total_reports), 0) * 100, 2) AS daily_flood_ratio_pct,
    count(CASE WHEN is_overflow THEN 1 END) AS overflow_khwaeng_hours
FROM curated.khwaeng_hourly
WHERE dt IN ('2026-09-24', '2026-09-25', '2026-09-26')
GROUP BY
    dt
ORDER BY
    dt;

-- Q2.3: รายชื่อ 10 แขวงแรกที่ได้รับผลกระทบจากน้ำท่วมสูงสุดในวันที่ฝนตกหนักสุด (2026-09-26)
SELECT
    khet,
    khwaeng,
    water_station_name,
    min(min_freeboard_m) AS min_freeboard_m,
    sum(total_reports) AS total_reports,
    sum(flood_reports) AS flood_reports,
    round(cast(sum(flood_reports) AS DOUBLE) / nullif(sum(total_reports), 0) * 100, 2) AS flood_ratio_pct
FROM curated.khwaeng_hourly
WHERE dt = '2026-09-26'
GROUP BY
    khet,
    khwaeng,
    water_station_name
ORDER BY
    flood_reports DESC
LIMIT 10;
