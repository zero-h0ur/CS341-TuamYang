"""
Dual-mode Storage Driver (Local Filesystem / AWS S3)
Owner: Member 1 (ThaiWater Ingestion)
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.config import DATA_DIR, S3_BUCKET_NAME, S3_REGION, STORAGE_BACKEND


class StorageDriver(ABC):
    """Abstract base storage driver."""

    @abstractmethod
    def save_json(self, key: str, data: Any, overwrite: bool = True) -> str:
        """Saves a JSON payload to the specified storage key/path."""
        pass

    @abstractmethod
    def load_json(self, key: str) -> Dict[str, Any]:
        """Loads and parses JSON payload from the specified key/path."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Checks if a file exists at the given key/path."""
        pass

    @abstractmethod
    def list_keys(self, prefix: str) -> List[str]:
        """Lists all keys starting with prefix."""
        pass


class LocalStorageDriver(StorageDriver):
    """Saves files to the local project data directory."""

    def __init__(self, base_dir: Path = DATA_DIR):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        normalized_key = key.replace("\\", "/").lstrip("/")
        return self.base_dir / normalized_key

    def save_json(self, key: str, data: Any, overwrite: bool = True) -> str:
        file_path = self._resolve_path(key)
        if file_path.exists() and not overwrite:
            return str(file_path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return str(file_path)

    def load_json(self, key: str) -> Dict[str, Any]:
        file_path = self._resolve_path(key)
        if not file_path.exists():
            raise FileNotFoundError(f"Key not found in local storage: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, key: str) -> bool:
        return self._resolve_path(key).exists()

    def list_keys(self, prefix: str) -> List[str]:
        prefix_path = self._resolve_path(prefix)
        if not prefix_path.exists():
            return []
        if prefix_path.is_file():
            return [prefix]

        results = []
        for p in prefix_path.rglob("*.json"):
            rel_key = p.relative_to(self.base_dir).as_posix()
            results.append(rel_key)
        return sorted(results)


class S3StorageDriver(StorageDriver):
    """Saves files directly to AWS S3 Bucket."""

    def __init__(self, bucket_name: str = S3_BUCKET_NAME, region_name: str = S3_REGION):
        self.bucket_name = bucket_name
        self.region_name = region_name
        try:
            import boto3
            self.s3_client = boto3.client("s3", region_name=self.region_name)
        except ImportError:
            raise ImportError("boto3 is required for S3StorageDriver. Install with 'pip install boto3'")

    def save_json(self, key: str, data: Any, overwrite: bool = True) -> str:
        s3_key = key.replace("\\", "/").lstrip("/")
        if not overwrite and self.exists(s3_key):
            return f"s3://{self.bucket_name}/{s3_key}"

        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=s3_key,
            Body=body,
            ContentType="application/json; charset=utf-8"
        )
        return f"s3://{self.bucket_name}/{s3_key}"

    def load_json(self, key: str) -> Dict[str, Any]:
        s3_key = key.replace("\\", "/").lstrip("/")
        resp = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
        content = resp["Body"].read().decode("utf-8")
        return json.loads(content)

    def exists(self, key: str) -> bool:
        s3_key = key.replace("\\", "/").lstrip("/")
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except Exception:
            return False

    def list_keys(self, prefix: str) -> List[str]:
        s3_prefix = prefix.replace("\\", "/").lstrip("/")
        paginator = self.s3_client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix):
            for item in page.get("Contents", []):
                keys.append(item["Key"])
        return sorted(keys)


def get_storage(backend: Optional[str] = None) -> StorageDriver:
    """Factory method to get the configured storage driver."""
    selected = (backend or STORAGE_BACKEND or "local").lower()
    if selected == "s3":
        return S3StorageDriver()
    return LocalStorageDriver()

