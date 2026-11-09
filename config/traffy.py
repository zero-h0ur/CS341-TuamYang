"""Configuration isolated from the existing ThaiWater ingestion."""

import os

API_URL = os.getenv(
    "TRAFFY_API_URL", "https://publicapi.traffy.in.th/share/teamchadchart/search"
)
PAGE_SIZE = 1000
HISTORY_START_DATE = "2022-06-01"
RAW_PREFIX = "raw/traffy"
INCOMING_PREFIX = "incoming/traffy"
ANNOTATED_PREFIX = "staged/traffy"
MANIFEST_PREFIX = "checkpoints/traffy"
