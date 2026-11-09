"""
CS341-TuamYang Configuration Module
Owner: Member 1 (ThaiWater Ingestion)
"""

import os
from pathlib import Path

# Base Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "reference"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Storage Configuration
# Mode: "local" (default for offline/local run) or "s3" (AWS Cloud / Lambda)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "cs341-tuamyang-data")
S3_REGION = os.getenv("AWS_REGION", "ap-southeast-1")

# ThaiWater API Configuration
THAIWATER_API_BASE = os.getenv(
    "THAIWATER_API_BASE",
    "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
)
WATERLEVEL_LOAD_ENDPOINT = f"{THAIWATER_API_BASE}/waterlevel_load"
WATERLEVEL_GRAPH_ENDPOINT = f"{THAIWATER_API_BASE}/waterlevel_graph"
RAIN_MONTHLY_GRAPH_ENDPOINT = f"{THAIWATER_API_BASE}/rain_monthly_graph"
RAIN_24H_GRAPH_ENDPOINT = f"{THAIWATER_API_BASE}/rain_24h_graph"

# Target Water Stations with bank height (min_bank)
WATER_STATIONS_FILE = REFERENCE_DIR / "stations_water.json"
RAIN_STATIONS_FILE = REFERENCE_DIR / "stations_rain.json"

# Timing & Quality Thresholds
MAX_FRESHNESS_HOURS = 2.0  # Alert if data is older than 2 hours
OUTLIER_FREEBOARD_DIFF_M = 5.0  # Flag if freeboard is excessively divergent (e.g. Phasi Charoen 7.42m anomaly)
MIN_WATERLEVEL_NON_NULL_RATIO = 0.30  # Minimum % non-null readings required for valid monthly waterlevel backfill
MIN_RAIN_NON_NULL_RATIO = 0.50  # Minimum % non-null daily records required for valid monthly rain backfill
BANK_HEIGHT_TOLERANCE_M = 0.05  # Tolerance between API reported min_bank and reference min_bank_msl

# S3 / Partition Paths
INCOMING_PREFIX = "incoming/thaiwater"
RAW_PREFIX = "raw/thaiwater"
WATERLEVEL_PREFIX = f"{RAW_PREFIX}/waterlevel"
RAIN_PREFIX = f"{RAW_PREFIX}/rain"

# Checkpoint Path for Backfill
CHECKPOINT_FILE = DATA_DIR / "checkpoints" / "backfill_checkpoint.json"

