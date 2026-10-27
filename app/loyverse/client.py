"""Read-only client. Never logs response bodies or authorization headers."""
from __future__ import annotations
import time
from pathlib import Path
import httpx
from dotenv import dotenv_values

class LoyverseError(Exception):
    def __init__(self, message, status_code=None, resource=None):
        super().__init__(message)
        self.status_code = status_code
        self.resource = resource

def accounts(path: str) -> dict[str, str]:
    if not path:
        return {}
    if not Path(path).is_file():
        raise LoyverseError("Loyverse token file is unavailable")
    values = dotenv_values(path)
    return {k: v.strip() for k, v in values.items() if k.startswith("LOYVERSE_TOKEN_") and v and v.strip()}

class Client:
    def __init__(self, token: str, transport=None, sleep=time.sleep):
        self.http = httpx.Client(base_url="https://api.loyverse.com/v1.0/", headers={"Authorization": "Bearer " + token}, timeout=30, transport=transport, follow_redirects=False)
        self.sleep = sleep

    def close(self):
        self.http.close()

    def pages(self, resource: str, params=None):
        if resource not in ("stores", "items", "receipts", "inventory", "employees", "categories", "suppliers", "merchant"):
            raise LoyverseError("Unsupported read-only resource")
        query = dict(params or {}); query["limit"] = 250
        seen = set()
        for _ in range(2000):
            for attempt in range(4):
                self.sleep(.55)
                try:
                    response = self.http.get(resource, params=query)
                except httpx.HTTPError:
                    if attempt < 3:
                        self.sleep(min(2 ** (attempt + 1), 15))
                        continue
                    raise LoyverseError("Loyverse connection failed; retrying on the next scheduled sync") from None
                if response.status_code == 429 or response.status_code >= 500:
                    self.sleep(min(2 ** (attempt + 1), 15)); continue
                break
            if response.status_code != 200:
                raise LoyverseError("Loyverse rejected the access token" if response.status_code in (401, 403) else f"Loyverse {resource} request failed (HTTP {response.status_code})", response.status_code, resource)
            try:
                body = response.json()
                rows = [body] if resource == "merchant" else body["inventory_levels" if resource == "inventory" else resource]
                if not isinstance(rows, list): raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise LoyverseError("Loyverse returned an invalid response") from None
            yield from rows
            cursor = body.get("cursor")
            if not cursor: return
            if not isinstance(cursor,str) or cursor in seen:
                raise LoyverseError("Loyverse pagination did not advance")
            seen.add(cursor); query["cursor"] = cursor
        raise LoyverseError("Loyverse pagination limit reached; sync was not applied")
