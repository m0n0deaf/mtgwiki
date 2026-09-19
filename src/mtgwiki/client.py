from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator

import requests


DEFAULT_API_URL = "https://mtg.wiki/api.php"
DEFAULT_USER_AGENT = "mtgwiki/1.2.2"


class APIError(RuntimeError):
    """Raised when MediaWiki returns an API-level error."""

    def __init__(self, code: str, info: str | None = None):
        self.code = code
        self.info = info or "Unknown MediaWiki API error"
        super().__init__(f"{code}: {self.info}")


class Client:
    """Polite, generic HTTP client for the MediaWiki Action API.

    The client deliberately knows nothing about the meaning of wiki content.
    It only handles transport concerns:

    * JSON GET requests
    * automatic MediaWiki continuation
    * serialized requests
    * minimum interval between real HTTP calls
    * ``maxlag``
    * ``Retry-After``
    * exponential backoff
    * bounded in-memory cache
    * optional persistent SQLite cache
    * request/cache statistics

    Parameters
    ----------
    api_url:
        URL to a MediaWiki ``api.php`` endpoint.
    user_agent:
        User-Agent header. For sustained use, MediaWiki recommends adding
        project/contact information.
    min_interval:
        Minimum number of seconds between *new* HTTP requests. The default
        ``0.5`` means at most about two requests per second when the server is
        fast. Cache hits do not touch the network.
    cache_ttl:
        Cache lifetime in seconds. Set to ``0`` to disable caching.
    cache_path:
        Optional SQLite file for cache reuse across Python processes. If not
        set, caching is memory-only.
    """

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        maxlag: int | None = 5,
        retries: int = 4,
        min_interval: float = 0.5,
        cache_ttl: float = 300.0,
        cache_max_entries: int = 512,
        cache_path: str | Path | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if retries < 0:
            raise ValueError("retries must be >= 0")
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        if cache_ttl < 0:
            raise ValueError("cache_ttl must be >= 0")
        if cache_max_entries < 0:
            raise ValueError("cache_max_entries must be >= 0")
        if not user_agent.strip():
            raise ValueError("user_agent must not be empty")

        self.api_url = api_url
        self.timeout = float(timeout)
        self.maxlag = maxlag
        self.retries = int(retries)
        self.min_interval = float(min_interval)
        self.cache_ttl = float(cache_ttl)
        self.cache_max_entries = int(cache_max_entries)
        self.cache_path = Path(cache_path) if cache_path else None

        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json;q=0.9,*/*;q=0.8",
            }
        )

        # One request/retry chain at a time. This keeps the client polite even
        # if a caller later invokes it from several threads.
        self._request_lock = threading.RLock()
        self._last_http_request_started: float | None = None

        # key -> (expires_at_monotonic, decoded_response)
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._sqlite: sqlite3.Connection | None = None
        if self.cache_path is not None and self.cache_ttl > 0:
            self._open_sqlite_cache()

        self._requests_total = 0
        self._http_requests = 0
        self._cache_hits = 0
        self._memory_cache_hits = 0
        self._disk_cache_hits = 0
        self._retries_performed = 0
        self._throttle_sleeps = 0
        self._throttle_sleep_seconds = 0.0
        self._retry_sleeps = 0
        self._retry_sleep_seconds = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def request(self, *, use_cache: bool = True, **params: Any) -> Any:
        """Perform one MediaWiki GET request and return decoded JSON.

        ``params`` are passed directly to MediaWiki. ``use_cache`` is local to
        this client and is never sent to the server.
        """
        request_params: dict[str, Any] = {
            "format": "json",
            "formatversion": 2,
            **params,
        }
        if self.maxlag is not None and "maxlag" not in request_params:
            request_params["maxlag"] = self.maxlag

        action = str(request_params.get("action", "query"))
        cacheable = use_cache and action in {"query", "parse", "paraminfo"}

        with self._request_lock:
            self._requests_total += 1
            cache_key: str | None = None

            if cacheable and self._cache_enabled:
                cache_key = self._cache_key(request_params)
                cached = self._cache_get(cache_key)
                if cached is not None:
                    self._cache_hits += 1
                    return cached

            data = self._request_uncached(request_params)

            if cache_key is not None:
                self._cache_put(cache_key, data)

            return copy.deepcopy(data)

    def iterate(self, **params: Any) -> Iterator[Any]:
        """Yield every batch of a continued MediaWiki request.

        All continuation values returned by MediaWiki are fed back into the
        next request automatically until no ``continue`` object remains.
        """
        base = dict(params)
        continuation: dict[str, Any] = {}

        while True:
            request_params = {**base, **continuation}
            data = self.request(**request_params)
            yield data

            if not isinstance(data, dict):
                break
            continuation = data.get("continue") or {}
            if not continuation:
                break

    def stats(self) -> dict[str, Any]:
        """Return request, cache and throttling statistics."""
        hit_percent = (
            self._cache_hits / self._requests_total * 100.0
            if self._requests_total
            else 0.0
        )
        return {
            "logical_requests": self._requests_total,
            "http_requests": self._http_requests,
            "cache_hits": self._cache_hits,
            "memory_cache_hits": self._memory_cache_hits,
            "disk_cache_hits": self._disk_cache_hits,
            "cache_hit_percent": round(hit_percent, 1),
            "cache_entries": len(self._cache),
            "persistent_cache": str(self.cache_path) if self.cache_path else None,
            "retries": self._retries_performed,
            "throttle_sleeps": self._throttle_sleeps,
            "throttle_sleep_seconds": round(self._throttle_sleep_seconds, 3),
            "retry_sleeps": self._retry_sleeps,
            "retry_sleep_seconds": round(self._retry_sleep_seconds, 3),
            "min_interval": self.min_interval,
            "maxlag": self.maxlag,
        }

    def clear_cache(self) -> None:
        """Clear both memory and optional persistent caches."""
        with self._request_lock:
            self._cache.clear()
            if self._sqlite is not None:
                self._sqlite.execute("DELETE FROM cache")
                self._sqlite.commit()

    def reset_stats(self) -> None:
        """Reset statistics without clearing cached responses."""
        with self._request_lock:
            self._requests_total = 0
            self._http_requests = 0
            self._cache_hits = 0
            self._memory_cache_hits = 0
            self._disk_cache_hits = 0
            self._retries_performed = 0
            self._throttle_sleeps = 0
            self._throttle_sleep_seconds = 0.0
            self._retry_sleeps = 0
            self._retry_sleep_seconds = 0.0

    def close(self) -> None:
        """Close the HTTP session and optional persistent cache."""
        with self._request_lock:
            if self._sqlite is not None:
                self._sqlite.close()
                self._sqlite = None
            close = getattr(self.session, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # HTTP / retries
    # ------------------------------------------------------------------
    def _request_uncached(self, request_params: dict[str, Any]) -> Any:
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            self._wait_for_http_slot()

            try:
                self._last_http_request_started = time.monotonic()
                self._http_requests += 1

                response = self.session.get(
                    self.api_url,
                    params=request_params,
                    timeout=self.timeout,
                )

                if response.status_code in {429, 502, 503, 504}:
                    if attempt >= self.retries:
                        response.raise_for_status()

                    self._retries_performed += 1
                    retry_after = response.headers.get("Retry-After")
                    minimum = 5.0 if response.status_code in {429, 503} else 1.0
                    self._retry_sleep(retry_after, attempt, minimum=minimum)
                    continue

                response.raise_for_status()
                data = response.json()

                error = data.get("error") if isinstance(data, dict) else None
                if error:
                    code = str(error.get("code", "apierror"))
                    info = str(error.get("info", "Unknown MediaWiki API error"))

                    if code in {"maxlag", "ratelimited"} and attempt < self.retries:
                        self._retries_performed += 1
                        self._retry_sleep(
                            response.headers.get("Retry-After"),
                            attempt,
                            minimum=5.0,
                        )
                        continue

                    raise APIError(code, info)

                return data

            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise
                self._retries_performed += 1
                self._retry_sleep(None, attempt, minimum=1.0)

        if last_error:
            raise last_error
        raise RuntimeError("Request failed without an exception")

    def _wait_for_http_slot(self) -> None:
        if self.min_interval <= 0 or self._last_http_request_started is None:
            return

        elapsed = time.monotonic() - self._last_http_request_started
        delay = self.min_interval - elapsed
        if delay <= 0:
            return

        self._throttle_sleeps += 1
        self._throttle_sleep_seconds += delay
        time.sleep(delay)

    def _retry_sleep(
        self,
        retry_after: str | None,
        attempt: int,
        *,
        minimum: float,
    ) -> None:
        seconds = self._retry_after_seconds(retry_after)
        if seconds is None:
            seconds = max(minimum, min(1.0 * (2**attempt), 60.0))
        else:
            seconds = max(minimum, seconds)

        if seconds <= 0:
            return

        self._retry_sleeps += 1
        self._retry_sleep_seconds += seconds
        time.sleep(seconds)

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        value = value.strip()
        if not value:
            return None

        try:
            return max(0.0, float(value))
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    @property
    def _cache_enabled(self) -> bool:
        return self.cache_ttl > 0 and self.cache_max_entries > 0

    def _open_sqlite_cache(self) -> None:
        assert self.cache_path is not None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        self._sqlite.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                expires REAL NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._sqlite.execute("DELETE FROM cache WHERE expires <= ?", (time.time(),))
        self._sqlite.commit()

    def _cache_key(self, params: dict[str, Any]) -> str:
        payload = {"api_url": self.api_url, "params": params}
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    def _cache_get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item is not None:
            expires_at, data = item
            if time.monotonic() < expires_at:
                self._cache.move_to_end(key)
                self._memory_cache_hits += 1
                return copy.deepcopy(data)
            del self._cache[key]

        if self._sqlite is None:
            return None

        row = self._sqlite.execute(
            "SELECT expires, payload FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None

        expires, payload = row
        if float(expires) <= time.time():
            self._sqlite.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._sqlite.commit()
            return None

        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            self._sqlite.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._sqlite.commit()
            return None

        self._disk_cache_hits += 1
        self._memory_cache_put(key, data, ttl=max(0.0, float(expires) - time.time()))
        return copy.deepcopy(data)

    def _cache_put(self, key: str, data: Any) -> None:
        if not self._cache_enabled:
            return

        self._memory_cache_put(key, data)

        if self._sqlite is not None:
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            expires = time.time() + self.cache_ttl
            self._sqlite.execute(
                "INSERT OR REPLACE INTO cache(key, expires, payload) VALUES (?, ?, ?)",
                (key, expires, payload),
            )
            self._sqlite.commit()

    def _memory_cache_put(self, key: str, data: Any, *, ttl: float | None = None) -> None:
        expires_at = time.monotonic() + (self.cache_ttl if ttl is None else ttl)
        self._cache[key] = (expires_at, copy.deepcopy(data))
        self._cache.move_to_end(key)

        while len(self._cache) > self.cache_max_entries:
            self._cache.popitem(last=False)
