"""
Structured Logger for CS341-TuamYang Ingestion
Outputs structured JSON logs to stdout (CloudWatch) and logs/ingestion.log.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from config.config import LOGS_DIR

LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOGS_DIR / "ingestion.log"


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_obj.update(record.extra_data)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


def setup_logger(name: str = "ingest_thaiwater") -> logging.Logger:
    """Configures and returns a structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if already setup
    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    # Console Handler (CloudWatch compatible)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler (for Evidence PR / local verification)
    try:
        file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # Fallback if file cannot be opened (e.g. read-only Lambda /tmp not used)
        logger.warning(f"Could not initialize file log handler: {e}")

    return logger


def log_ingestion_metric(logger: logging.Logger, batch_id: str, data_type: str, record_count: int,
                         duration_sec: float, destination: str, status: str = "SUCCESS", error: str = None):
    """Logs structured metrics for data ingestion monitoring."""
    extra = {
        "event_type": "INGESTION_METRIC",
        "batch_id": batch_id,
        "data_type": data_type,
        "record_count": record_count,
        "duration_sec": round(duration_sec, 3),
        "destination": destination,
        "status": status,
    }
    if error:
        extra["error"] = error
    logger.info(f"Ingestion completed: {data_type} ({record_count} records) -> {destination}", extra={"extra_data": extra})

