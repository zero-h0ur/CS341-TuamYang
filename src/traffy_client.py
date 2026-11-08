"""Small stdlib-only client for the public Bangkok Traffy search endpoint.

The legacy endpoint accepts inclusive UTC *dates*, not timestamp ranges.
Keep date conversion/filtering in the ingestion layer, not in the request.
"""

import json
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.traffy import API_URL, PAGE_SIZE


class TraffyAPIError(RuntimeError):
    """Fetching a complete, validated source response was not possible."""


class TraffyClient:
    def __init__(self, api_url=API_URL, page_size=PAGE_SIZE, timeout=30,
                 retries=3, max_pages=100, sleep=time.sleep, opener=urlopen):
        if not 1 <= page_size <= 1000 or retries < 0 or max_pages < 1:
            raise ValueError("page_size must be 1..1000; retries >= 0; max_pages >= 1")
        self.api_url = api_url
        self.page_size = page_size
        self.timeout = timeout
        self.retries = retries
        self.max_pages = max_pages
        self.sleep = sleep
        self.opener = opener

    def _request_json(self, params):
        request = Request(
            self.api_url + "?" + urlencode(params),
            headers={"User-Agent": "CS341-TuamYang/1.0 (academic ingestion)",
                     "Accept": "application/json"},
        )
        for attempt in range(self.retries + 1):
            retry_after = None
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise TraffyAPIError("Source JSON must be an object")
                if payload.get("status") is not True:
                    raise TraffyAPIError("Source returned a non-success status")
                return payload
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise TraffyAPIError(f"HTTP {exc.code} from Traffy") from exc
                error = f"HTTP {exc.code}"
                value = exc.headers.get("Retry-After", "") if exc.headers else ""
                if value.isdigit():
                    retry_after = min(int(value), 60)
            except (URLError, TimeoutError, OSError) as exc:
                error = str(exc)
            except (ValueError, UnicodeError) as exc:
                error = f"Invalid source JSON: {exc}"
            if attempt == self.retries:
                raise TraffyAPIError(f"Traffy request failed after {attempt + 1} attempts: {error}")
            self.sleep(retry_after if retry_after is not None else min(2 ** attempt, 8))

    def iter_pages(self, start_date, end_date, remaining_ms=None, allow_total_change=False):
        """Yield pages through an explicit empty response, or raise.

        The live legacy API can overstate total and uses total=null on its
        terminal empty page. Treat totals as advisory, retain any discrepancy,
        and never use a short page alone as the end-of-data signal.
        """
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
        if start > end:
            raise ValueError("start_date must be <= end_date")
        offset, expected = 0, None
        seen_pages = set()
        for page_index in range(self.max_pages):
            budget_ms = (self.retries + 1) * self.timeout * 1000 + 70000
            if remaining_ms is not None and remaining_ms() < budget_ms:
                raise TraffyAPIError("Insufficient Lambda time to fetch another page safely")
            params = {"start": start.isoformat(), "end": end.isoformat(),
                      "limit": self.page_size, "offset": offset}
            payload = self._request_json(params)
            rows, total = payload.get("results"), payload.get("total")
            if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                raise TraffyAPIError("results must be a list of report objects")
            if not rows and total in (None, 0):
                yield {"index": page_index, "params": params, "response": payload,
                       "source_exhausted": True}
                return
            if type(total) is not int or total < 0:
                raise TraffyAPIError("total must be a non-negative integer")
            if expected is None:
                expected = total
            elif expected != total and not allow_total_change:
                raise TraffyAPIError("Source total changed during pagination; retry the date window")
            if (offset + len(rows) > total and not allow_total_change) or not rows:
                raise TraffyAPIError("Page size/total mismatch or premature empty page")
            signature = json.dumps(rows, sort_keys=True, ensure_ascii=False)
            if rows and signature in seen_pages:
                raise TraffyAPIError("Source repeated a page; pagination may be ignored")
            seen_pages.add(signature)
            yield {"index": page_index, "params": params, "response": payload,
                   "source_exhausted": False}
            offset += len(rows)
            self.sleep(0.2)
        raise TraffyAPIError(f"Exceeded max_pages={self.max_pages}; dataset is incomplete")
