"""Test fixtures: a throw-away database per test and a fake StoreHub served through httpx.MockTransport."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as dbm  # noqa: E402
from app.config import Config, set_config  # noqa: E402
from app.dbinit import init_db  # noqa: E402
from app.storehub.client import StoreHubClient  # noqa: E402
from app.storehub.sync import SyncService  # noqa: E402


class _NoWait:
    def wait(self):
        pass


@pytest.fixture()
def cfg(tmp_path):
    c = Config(loyverse_reporting_enabled=False, loyverse_tokens_file="", data_dir=tmp_path, storehub_username="test", storehub_token="secret", scheduler_enabled=False, secret_key="k" * 32)
    set_config(c)
    dbm.init_engine()
    init_db()
    return c


@pytest.fixture()
def session(cfg):
    s = dbm.SessionLocal()
    yield s
    s.close()


class FakeStoreHub:
    """Serves StoreHub-shaped JSON. Mutate .data between syncs to simulate changes in StoreHub."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail_next: list[int] = []  # status codes to return before succeeding
        self.data = {
            "stores": [
                {"id": "S_WH", "name": "TheDailyCentro Warehouse", "city": "Mandaue"},
                {"id": "S_A", "name": "The Daily Centro - Alpha", "city": "Talisay"},
                {"id": "S_B", "name": "The Daily Centro - Beta", "city": "Bantayan"},
                {"id": "S_X", "name": "Some Other Shop", "city": "Cebu"},
            ],
            "employees": [{"id": "E1", "firstName": "Ana", "lastName": "Reyes", "email": "a@x.com", "phone": "1"}],
            "products": [
                {"id": "P1", "name": "Cola 500ml", "barcode": "111", "category": "Drinks", "unitPrice": 20.0, "cost": 12.0, "trackStockLevel": True, "isParentProduct": False, "priceType": "Fixed"},
                {"id": "P2", "name": "Chips", "barcode": "222", "sku": "CH1", "category": "Snacks", "unitPrice": 10.0, "cost": 6.0, "trackStockLevel": True, "isParentProduct": False, "priceType": "Fixed"},
                {"id": "P3", "name": "Untracked Service", "unitPrice": 50.0, "trackStockLevel": False, "isParentProduct": False, "priceType": "Fixed"},
            ],
            "inventory": {"S_WH": [{"productId": "P1", "quantityOnHand": 100}, {"productId": "P2", "quantityOnHand": 40}],
                          "S_A": [{"productId": "P1", "quantityOnHand": 10, "warningStock": 5}, {"productId": "P2", "quantityOnHand": -3}],
                          "S_B": [{"productId": "P1", "quantityOnHand": 0}, {"productId": "P2", "quantityOnHand": 8}]},
            "transactions": [],
            "purchaseOrders": [],
            "timesheets": [],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.calls.append((path, params))
        assert request.method == "GET", "the system must never write to StoreHub"
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text="boom")
        d = self.data
        if path == "/stores":
            return httpx.Response(200, json=d["stores"])
        if path == "/employees":
            return httpx.Response(200, json=d["employees"])
        if path == "/products":
            return httpx.Response(200, json=d["products"])
        if path.startswith("/inventory/"):
            sid = path.rsplit("/", 1)[1]
            if sid not in d["inventory"]:
                return httpx.Response(404, text="no store")
            return httpx.Response(200, json=d["inventory"][sid])
        if path == "/transactions":
            return httpx.Response(200, json=self._tx_window(params))
        if path == "/purchaseOrders":
            return httpx.Response(200, json=d["purchaseOrders"])
        if path == "/timesheets":
            return httpx.Response(200, json=d["timesheets"])
        return httpx.Response(404, text="unknown")

    def _tx_window(self, params):
        from app.timeutil import parse_iso
        a, b = parse_iso(params["from"]), parse_iso(params["to"])
        return [t for t in self.data["transactions"] if a <= parse_iso(t["transactionTime"]) <= b]

    def factory(self):
        def make(sink):
            c = StoreHubClient(transport=httpx.MockTransport(self.handler), error_sink=sink, sleep=lambda s: None)
            c.limiter = _NoWait()
            return c
        return make


@pytest.fixture()
def fake():
    return FakeStoreHub()


@pytest.fixture()
def sync(cfg, fake):
    return SyncService(client_factory=fake.factory())


def tx(ref, store, time_iso, items, total=None, cancelled=False, ttype="Sale", pay="Cash", **kw):
    """items: list of (productId, qty, line_total)"""
    total = total if total is not None else sum(i[2] for i in items)
    d = {"refId": ref, "invoiceNumber": ref, "storeId": store, "transactionType": ttype, "transactionTime": time_iso, "total": total,
         "subTotal": round(total / 1.12, 2), "tax": round(total - total / 1.12, 2), "discount": 0, "roundedAmount": 0, "serviceCharge": 0,
         "isCancelled": cancelled, "channel": "OFFLINE_PAYMENTS", "terminalNumber": 1, "employeeId": "E1",
         "items": [{"productId": p, "quantity": q, "total": t, "subTotal": round(t / 1.12, 2), "unitPrice": t / q, "itemType": "Item", "taxCode": "VAT", "discount": 0} for p, q, t in items],
         "payments": [{"paymentMethod": pay, "amount": total}]}
    if cancelled:
        d["cancelledTime"] = time_iso
        d["returnReason"] = "Wrong payment type"
    d.update(kw)
    return d
