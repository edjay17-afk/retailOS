"""StoreHub REST client: read-only, rate limited, with retries and error logging."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import httpx

from ..config import Config, get_config

log = logging.getLogger("retailos.storehub")


class StoreHubError(Exception):
    pass


class AuthError(StoreHubError):
    """401/403: stop the job and alert, never retry."""


class NotFound(StoreHubError):
    pass


class ApiError(StoreHubError):
    pass


class RateLimiter:
    """Token bucket with one token at a time: calls are spaced at least 1/rps seconds apart, process wide."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def shared_limiter(rps: float) -> RateLimiter:
    global _limiter
    with _limiter_lock:
        if _limiter is None or abs(_limiter.min_interval - 1.0 / rps) > 1e-9:
            _limiter = RateLimiter(rps)
        return _limiter


class StoreHubClient:
    """Reads are the everyday use; the single write is create_product (new-product entry pushes straight to StoreHub)."""

    MAX_ATTEMPTS = 5

    def __init__(self, cfg: Config | None = None, transport: httpx.BaseTransport | None = None,
                 error_sink: Callable[[dict], None] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg or get_config()
        self.http = httpx.Client(base_url=self.cfg.storehub_base_url, auth=(self.cfg.storehub_username, self.cfg.storehub_token),
                                 timeout=httpx.Timeout(120.0, connect=15.0), transport=transport,
                                 headers={"Accept": "application/json", "User-Agent": "RetailOS/1.0"})
        self.limiter = shared_limiter(self.cfg.storehub_rps)
        self.error_sink = error_sink
        self.sleep = sleep
        self.calls = 0
        self.retries = 0

    def close(self) -> None:
        self.http.close()

    def _record_error(self, path: str, params: dict | None, status: int | None, cls: str, attempt: int, ms: int, body: str | None):
        if self.error_sink:
            try:
                self.error_sink({"endpoint": path, "params": None if not params else str(params)[:300], "status": status,
                                 "error_class": cls, "attempt": attempt, "duration_ms": ms,
                                 "body_excerpt": (body or "")[:2000]})
            except Exception:  # logging must never break a sync
                log.exception("error sink failed")

    def get(self, path: str, params: dict | None = None) -> Any:
        """GET with back-off. 401/403 stop at once, 404 raises NotFound, 429/5xx/timeouts retry."""
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self.limiter.wait()
            started = time.monotonic()
            self.calls += 1
            try:
                resp = self.http.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ms = int((time.monotonic() - started) * 1000)
                self._record_error(path, params, None, type(e).__name__, attempt, ms, str(e))
                last_exc = e
                self.retries += 1
                self.sleep(min(2 ** attempt, 30))
                continue
            ms = int((time.monotonic() - started) * 1000)
            sc = resp.status_code
            if sc == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    self._record_error(path, params, sc, "BadJson", attempt, ms, resp.text[:500])
                    raise ApiError(f"{path}: response was not valid JSON") from e
            if sc in (401, 403):
                self._record_error(path, params, sc, "AuthError", attempt, ms, resp.text)
                raise AuthError(f"{path}: HTTP {sc} (check the StoreHub username and token)")
            if sc == 404:
                self._record_error(path, params, sc, "NotFound", attempt, ms, resp.text)
                raise NotFound(path)
            if sc == 429 or sc >= 500:
                self._record_error(path, params, sc, "Retryable", attempt, ms, resp.text)
                last_exc = ApiError(f"{path}: HTTP {sc}")
                self.retries += 1
                self.sleep(min(5 * attempt if sc == 429 else 2 ** attempt, 60))
                continue
            self._record_error(path, params, sc, "ClientError", attempt, ms, resp.text)
            raise ApiError(f"{path}: HTTP {sc}")
        raise ApiError(f"{path}: failed after {self.MAX_ATTEMPTS} attempts ({last_exc})")

    # ---- endpoint helpers (all read only)
    def stores(self): return self.get("/stores")
    def employees(self): return self.get("/employees")
    def products(self): return self.get("/products")
    def inventory(self, store_id: str): return self.get(f"/inventory/{store_id}")
    def transactions(self, frm: str, to: str, store_id: str | None = None):
        p = {"from": frm, "to": to, "includeOnline": "true"}
        if store_id:
            p["storeId"] = store_id
        return self.get("/transactions", p)
    def purchase_orders(self, frm: str, to: str): return self.get("/purchaseOrders", {"from": frm, "to": to})
    def timesheets(self, frm: str, to: str, store_id: str | None = None):
        p = {"from": frm, "to": to}
        if store_id:
            p["storeId"] = store_id
        return self.get("/timesheets", p)

    # ---- the one write: create a product (partner API: POST /products returns the created product)
    def create_product(self, payload: dict) -> dict:
        """POST /products with the same throttling and back-off as reads. 401/403 stop at once, 429/5xx/timeouts retry;
        any other 4xx (a duplicate barcode or SKU, a bad field) raises ApiError carrying StoreHub's own message."""
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self.limiter.wait()
            started = time.monotonic()
            self.calls += 1
            try:
                resp = self.http.post("/products", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ms = int((time.monotonic() - started) * 1000)
                self._record_error("/products", payload, None, type(e).__name__, attempt, ms, str(e))
                last_exc = e
                self.retries += 1
                self.sleep(min(2 ** attempt, 30))
                continue
            ms = int((time.monotonic() - started) * 1000)
            sc = resp.status_code
            if sc in (200, 201):
                try:
                    return resp.json()
                except ValueError as e:
                    self._record_error("/products", payload, sc, "BadJson", attempt, ms, resp.text[:500])
                    raise ApiError("POST /products: response was not valid JSON") from e
            if sc in (401, 403):
                self._record_error("/products", payload, sc, "AuthError", attempt, ms, resp.text)
                raise AuthError(f"POST /products: HTTP {sc} (check the StoreHub username and token)")
            if sc == 429 or sc >= 500:
                self._record_error("/products", payload, sc, "Retryable", attempt, ms, resp.text)
                last_exc = ApiError(f"POST /products: HTTP {sc}")
                self.retries += 1
                self.sleep(min(5 * attempt if sc == 429 else 2 ** attempt, 60))
                continue
            self._record_error("/products", payload, sc, "ClientError", attempt, ms, resp.text)
            raise ApiError(f"POST /products: HTTP {sc}: {resp.text[:300]}")
        raise ApiError(f"POST /products: failed after {self.MAX_ATTEMPTS} attempts ({last_exc})")
