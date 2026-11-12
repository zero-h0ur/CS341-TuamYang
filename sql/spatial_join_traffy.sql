-- ==============================================================================
-- CS341 TuamYang: Athena Spatial Join (ST_Contains) for Traffy Fondue Reports
-- Issue #3: [Reference + Spatial join] ขอบเขตแขวง และการจับคู่แขวง ↔ สถานี
-- Owner: นายภูรินทร์ แก้วพ่วงเสก (PhurinKaewpuangsek)
--
-- Purpose:
--   1. Define Glue / Athena External Tables for `reference.khwaeng_boundaries`
--      (Royal Thai Survey Department ADM3 polygons in WKT format via HDX) and
--      `reference.khwaeng_station_mapping` (ward <-> water & rain station mapping).
--   2. Assign an official Bangkok khwaeng (subdistrict) and khet (district) to each
--      Traffy Fondue report in `trusted.traffy` from its `(longitude, latitude)`
--      coordinates using Athena's Geospatial engine (`ST_Contains`, `ST_GeometryFromText`,
--      `ST_Point`), falling back to address regex extraction only when coordinates
--      fall on a river/boundary edge.
--   3. Measure spatial join success rate (% of Traffy reports joined to a khwaeng).
-- ==============================================================================

CREATE DATABASE IF NOT EXISTS reference;
CREATE DATABASE IF NOT EXISTS trusted;
CREATE DATABASE IF NOT EXISTS curated;

-- ------------------------------------------------------------------------------
-- 1. Reference Table: Bangkok Khwaeng Boundaries (WKT Polygons from RTSD / HDX)
-- Source file: reference/khwaeng_boundaries.csv
-- ------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS reference.khwaeng_boundaries (
    khwaeng_code STRING COMMENT 'ADM3 P-code (TH10xxxx) from Royal Thai Survey Department',
    name_th STRING COMMENT 'Subdistrict (khwaeng) name in Thai',
    name_en STRING COMMENT 'Subdistrict (khwaeng) name in English',
    khet STRING COMMENT 'District (khet) name in Thai',
    khet_code STRING COMMENT 'ADM2 P-code (TH10xx)',
    khet_en STRING COMMENT 'District (khet) name in English',
    center_lat DOUBLE COMMENT 'Representative latitude of the ward',
    center_lon DOUBLE COMMENT 'Representative longitude of the ward',
    area_sqkm DOUBLE COMMENT 'Polygon area in square kilometers',
    wkt STRING COMMENT 'OGC Well-Known Text (POLYGON / MULTIPOLYGON) geometry in WGS84 (CRS84 lon/lat)'
)
COMMENT 'Bangkok subdistrict (khwaeng) boundaries from Royal Thai Survey Department via HDX'
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES (
    'separatorChar' = ',',
    'quoteChar' = '"',
    'escapeChar' = '\\'
)
STORED AS TEXTFILE
LOCATION 's3://cs341-tuamyang-data/reference/khwaeng_boundaries/'
TBLPROPERTIES ('skip.header.line.count' = '1');


-- ------------------------------------------------------------------------------
-- 2. Reference Table: Ward <-> Water Station & Nearest Rain Station Mapping
-- Source file: reference/khwaeng_station_mapping.csv
-- Compatible with PR #13 (`sql/create_khwaeng_hourly.sql` JOIN on khwaeng, khet)
-- and enrichable via `khwaeng_code`.
-- ------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS reference.khwaeng_station_mapping (
    khwaeng_code STRING COMMENT 'ADM3 P-code (TH10xxxx)',
    khwaeng STRING COMMENT 'Subdistrict name in Thai (join key for curated.khwaeng_hourly)',
    name_th STRING COMMENT 'Subdistrict name in Thai',
    name_en STRING COMMENT 'Subdistrict name in English',
    khet STRING COMMENT 'District name in Thai (join key for curated.khwaeng_hourly)',
    khet_code STRING COMMENT 'ADM2 P-code (TH10xx)',
    khet_en STRING COMMENT 'District name in English',
    center_lat DOUBLE COMMENT 'Ward representative latitude',
    center_lon DOUBLE COMMENT 'Ward representative longitude',
    within_radius_km BOOLEAN COMMENT 'True if ward polygon is within default 1.0 km radius of any water station',
    radius_threshold_km DOUBLE COMMENT 'Configured radius parameter in km (default 1.0)',
    min_water_boundary_dist_km DOUBLE COMMENT 'Minimum distance from ward polygon boundary to nearest water station (km)',
    within_1km_water_station_ids STRING COMMENT 'Semicolon-delimited water station IDs within radius',
    containing_water_station_id STRING COMMENT 'Water station ID(s) physically inside this ward polygon via ST_Contains',
    paired_water_station_id INT COMMENT 'ID of paired (nearest) water-level station',
    water_station_code STRING COMMENT 'Short code of paired water station (e.g. BKK021, C.12)',
    water_station_name STRING COMMENT 'Thai name of paired water station',
    canal_name STRING COMMENT 'Canal / river name in Thai',
    water_station_dist_km DOUBLE COMMENT 'Haversine distance from ward center to paired water station (km)',
    water_quality_flag STRING COMMENT 'NORMAL or SUSPECT_DATUM_MISMATCH',
    water_usable_for_freeboard BOOLEAN COMMENT 'False for Station 10 (SUSPECT_DATUM_MISMATCH)',
    paired_rain_station_id INT COMMENT 'ID of nearest usable rain station (usable_for_rainfall = true)',
    rain_station_code STRING COMMENT 'Short code of paired rain station',
    rain_station_name STRING COMMENT 'Thai name of paired rain station',
    rain_station_dist_km DOUBLE COMMENT 'Haversine distance from ward center to paired rain station (km)',
    rain_boundary_dist_km DOUBLE COMMENT 'Distance from ward boundary to paired rain station (km)',
    rain_quality_flag STRING COMMENT 'OK or SUSPECT_RAIN_SENSOR_OUTAGE',
    rain_usable_for_rainfall BOOLEAN COMMENT 'Always True for paired stations (Station 852087 excluded)'
)
COMMENT 'Ward to water-station and nearest usable rain-station mapping table'
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES (
    'separatorChar' = ',',
    'quoteChar' = '"',
    'escapeChar' = '\\'
)
STORED AS TEXTFILE
LOCATION 's3://cs341-tuamyang-data/reference/khwaeng_station_mapping/'
TBLPROPERTIES ('skip.header.line.count' = '1');


-- ------------------------------------------------------------------------------
-- 3. Spatial Join Query (`ST_Contains`): Assign Khwaeng to Each Traffy Report
-- Note:
--   - Athena / Trino geospatial functions use (longitude, latitude) coordinate order:
--     `ST_Point(t.longitude, t.latitude)` and `ST_GeometryFromText(b.wkt)`.
--   - Preserves all reports in `trusted.traffy` (full Q2 denominator: flood,
--     non_flood, and uncertain) via LEFT JOIN.
-- ------------------------------------------------------------------------------
WITH traffy_dedup AS (
    SELECT
        ticket_id,
        description,
        address,
        longitude,
        latitude,
        reported_at_utc,
        reported_at_bkk,
        timestamp_epoch,
        report_date_bkk AS dt,
        report_hour_bkk AS hour,
        flood_classification,
        is_flood_report,
        quality_flags,
        row_number() OVER (
            PARTITION BY ticket_id
            ORDER BY ingested_at_utc DESC, batch_id DESC
        ) AS rn
    FROM trusted.traffy
),
ward_polygons AS (
    SELECT
        khwaeng_code,
        name_th AS khwaeng,
        name_en AS khwaeng_en,
        khet,
        khet_code,
        ST_GeometryFromText(wkt) AS geom
    FROM reference.khwaeng_boundaries
    WHERE wkt IS NOT NULL
),
traffy_spatial_joined AS (
    SELECT
        t.ticket_id,
        t.dt,
        t.hour,
        t.latitude,
        t.longitude,
        t.address,
        t.flood_classification,
        t.is_flood_report,
        w.khwaeng_code,
        coalesce(
            w.khwaeng,
            nullif(regexp_extract(t.address, 'แขวง([^\s,]+)', 1), ''),
            'ไม่ระบุ'
        ) AS khwaeng,
        coalesce(
            w.khet,
            nullif(regexp_extract(t.address, 'เขต([^\s,]+)', 1), ''),
            'ไม่ระบุ'
        ) AS khet,
        CASE
            WHEN w.khwaeng_code IS NOT NULL THEN 'ST_CONTAINS'
            WHEN nullif(regexp_extract(t.address, 'แขวง([^\s,]+)', 1), '') IS NOT NULL THEN 'ADDRESS_FALLBACK'
            ELSE 'UNMATCHED'
        END AS spatial_join_method
    FROM traffy_dedup t
    LEFT JOIN ward_polygons w
        ON t.longitude IS NOT NULL
       AND t.latitude IS NOT NULL
       AND ST_Contains(w.geom, ST_Point(t.longitude, t.latitude))
    WHERE t.rn = 1
)
SELECT
    j.ticket_id,
    j.dt,
    j.hour,
    j.latitude,
    j.longitude,
    j.khwaeng_code,
    j.khwaeng,
    j.khet,
    j.spatial_join_method,
    j.flood_classification,
    j.is_flood_report,
    m.within_radius_km,
    m.paired_water_station_id,
    m.water_station_name,
    m.canal_name,
    m.water_station_dist_km,
    m.water_usable_for_freeboard,
    m.paired_rain_station_id,
    m.rain_station_name,
    m.rain_station_dist_km
FROM traffy_spatial_joined j
LEFT JOIN reference.khwaeng_station_mapping m
    ON j.khwaeng_code = m.khwaeng_code;


-- ------------------------------------------------------------------------------
-- 4. Spatial Join Coverage Audit Query (% of Traffy Reports Joined to a Khwaeng)
-- ------------------------------------------------------------------------------
/*
WITH traffy_dedup AS (
    SELECT
        ticket_id,
        report_date_bkk AS dt,
        longitude,
        latitude,
        address,
        row_number() OVER (
            PARTITION BY ticket_id
            ORDER BY ingested_at_utc DESC, batch_id DESC
        ) AS rn
    FROM trusted.traffy
),
ward_polygons AS (
    SELECT
        khwaeng_code,
        name_th,
        khet,
        ST_GeometryFromText(wkt) AS geom
    FROM reference.khwaeng_boundaries
)
SELECT
    t.dt,
    count(*) AS total_reports,
    count(CASE WHEN t.latitude IS NOT NULL AND t.longitude IS NOT NULL THEN 1 END) AS valid_coord_reports,
    count(w.khwaeng_code) AS st_contains_matched_reports,
    round(cast(count(w.khwaeng_code) AS DOUBLE) / nullif(count(*), 0) * 100.0, 2) AS st_contains_match_pct
FROM traffy_dedup t
LEFT JOIN ward_polygons w
    ON t.longitude IS NOT NULL
   AND t.latitude IS NOT NULL
   AND ST_Contains(w.geom, ST_Point(t.longitude, t.latitude))
WHERE t.rn = 1
GROUP BY t.dt
ORDER BY t.dt;
*/
