# QA follow-up: QA-2026-10-20.1

Prepared 20 October 2026 (Asia/Manila).

The tested code is loaded in the running local application; its read-only /healthz check returned 200. The normal configured scheduler resumes after restart. No live synchronization or business-data mutation was invoked as a QA test. Trusted staff-facing HTTPS activation remains pending.

## Scope and completion plan

| Work | Target | Result |
| --- | --- | --- |
| Sales-only briefing, automatic suggestions, report scope, date validation, Stock changes text | 20 Oct 2026 | Implemented |
| Isolated regression tests and screenshot handoff | 21 Oct 2026 | Completed early on 20 Oct |
| HTTPS application/deployment configuration | 20 Oct 2026 | Prepared and tested in isolation |
| Trusted certificate and staff-facing HTTPS activation | Within one working day after domain/DNS/server access is available | Pending infrastructure details; not claimed live |
| Password-labelled values/storage/password changes | Excluded by the requester | Not audited or changed in this work |

## What changed

- Dashboard and Auto replenishment automatically calculate suggestions from locally synced data on first visit, then at most once per five minutes. The background job also checks every five minutes after startup. No manual Refresh is required. Existing refresh remains available. Generating suggestions does not synchronize StoreHub, approve an order, create a transfer, or send stock to StoreHub. Saved quantity edits are preserved.
- Required stale source data still blocks generation. The page displays the blocked run and its reason; it does not fabricate suggestions. Changes in replenishment settings invalidate the five-minute reuse period.
- The stock-cover panel now explains that cover is not used in sales-only replenishment. It no longer interprets missing cover as no pending suggestions. In stock-and-sales mode it distinguishes absent cover from an empty suggestion queue.
- Deterministic and AI briefing context now use the active calculation mode. Sales-only quantity is max(0, average daily sales x configured cover days - incoming), followed by pack/MOQ/cap rules. Stock on hand and lead-time stock cover are not quantity inputs in this mode. Stock cover is unavailable, not zero. Historical briefs from an older prompt or different mode/cover period are excluded from the current briefing; the audit record remains. Generate a new brief to replace it.
- Product detail links now retain the selected location and exact preset/custom period. A one-day report stays one day. Scope is displayed. The All products link still returns to the original list filters. Changing the detail period deliberately affects that detail view; returning to the list restores the original list selection.
- Sales, sales by product, product detail, Transactions, and sales by employee now reject malformed, reversed, future, and oversized custom ranges with HTTP 400 and an explicit explanation. The shared calendar also displays an inline error and does not submit invalid dates. The consistent custom maximum is 401 inclusive calendar days, matching the existing Sales maximum. From = To is valid. Dates use the application's Philippine business date.
- The Ask AI launcher is hidden while the period picker is open so it cannot cover Apply on mobile.
- Stock changes explanatory text is clean UTF-8. An event remains a record of a physical change; stock in StoreHub changes only after the matching action is entered there and synchronized.
- REQUIRE_HTTPS=true enables a trusted-origin redirect for plaintext GETs, rejects plaintext writes before processing, forces Secure session cookies, and emits HSTS on HTTPS. Cookies retain HttpOnly and SameSite=Lax. The production setup script enables the flag and sets BASE_URL. Current HTTP access is not represented as HTTPS-complete.

## Isolated verification

All mutation and synchronization tests used per-test temporary databases with the scheduler disabled and mocked StoreHub. Browser checks used a FastAPI TestClient bridge and blocked external HTTPS requests; no live AI provider was invoked. No production user/account/password or production business data appears in screenshots.

Regression command:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_qa_followup.py tests/test_sales_product_supplier.py tests/test_sales_day_drilldown.py tests/test_ai.py tests/test_web.py tests/test_replenishment_sheets.py -q
```

Result: 99 passed. Framework deprecation warnings remain; no failing tests.

Browser command:

```powershell
.venv/Scripts/python.exe -m tests.qa_browser_check
```

Result: 1 passed. Verified desktop 1440px and mobile 390px/320px picker bounds, invalid input remaining visible, Apply/Cancel, Today preset, product scope, sales-only cover message, and no JavaScript page errors. `node --check app/web/static/daterange.js` passed.

Checks include:

1. Open dashboard with fresh mock source data and no generated suggestions. Suggestions appear without a manual refresh or any extra mock API calls. Reopening uses the recent result. A later recomputation preserves saved quantities.
2. Open with missing/stale mock source data. Generation is blocked and creates no suggestions.
3. Select a store and Today, open a product, and compare against that store's synthetic total (123.45); another store's 876.54 must not appear. Repeat for a custom single-day range and use All products to restore list filters.
4. Test malformed, reversed, future and >401-day ranges on all five report routes; expect explicit HTTP 400. A valid single-day range succeeds. In the shared picker, type an impossible date; expect inline error and no navigation.
5. Compare filtered summary/detailed CSVs and product reports using test data; product and grand totals and per-day column sums remain consistent.
6. Use dedicated temporary store_manager/staff accounts: another store's product detail/replenishment is 403. Store-bound staff cannot open Sync or Sales. Existing staff access to their own replenishment is retained.
7. Enable HTTPS requirement only in the test configuration: HTTP login GET redirects to the configured trusted origin even with a forged Host/forwarded-proto header; HTTP POST is rejected; HTTPS login produces Secure/HttpOnly/SameSite=Lax session cookie and HSTS. This does not verify a real public certificate.
8. Change replenishment mode after generating a fake-model brief; the older-policy brief is not returned as current.

## Screenshot evidence (synthetic fixtures)

- [Product detail retains store and day](product-scope.png)
- [Invalid date, desktop](invalid-date-desktop.png)
- [Invalid date, mobile](invalid-date-mobile.png)
- [Automatic suggestions](automatic-suggestions.png)
- [Sales-only stock cover explanation](sales-only-cover.png)
- [Stock changes text](stock-changes-text.png)

## HTTPS activation and support needed

Provide the intended staff-facing hostname and the owner of DNS/server access. No passwords should be sent in chat or screenshots. The prepared deployment uses Caddy with a public hostname, DNS pointing to the server, reachable ports 80/443, and the app bound to 127.0.0.1:8000. Caddy obtains/renews certificates and redirects HTTP; see [Caddy automatic HTTPS requirements](https://caddyserver.com/docs/automatic-https).

Set HOST=127.0.0.1, BEHIND_PROXY=true, BASE_URL=https://the-approved-hostname and REQUIRE_HTTPS=true only after configuring the front door. Uvicorn already trusts proxy headers only from 127.0.0.1. Do not expose the app port directly. Use the existing deploy/Caddyfile and setup script for the supported VM deployment, or adapt to the actual Windows hosting arrangement after confirmation.

Acceptance on the real hostname: verify a trusted certificate chain with no browser warning; HTTP redirects; HTTPS login works; inspect cookie flags without copying their values; access from a staff device; verify direct port 8000 is inaccessible externally. Keep certificate renewal enabled. This remains pending, not a passed check.

## Business rules for agreement and remaining QA

- Existing permissions let staff view and act on replenishment for their assigned stores. No permission expansion was introduced. Confirm that this is the intended policy before wider access.
- Automatic refresh creates/recalculates recommendations only. Manager approval and existing transfer/order workflows remain separate. Source freshness is still required even in sales-only mode.
- Returning to the product list restores the original list filters, even if the detail period was subsequently changed.
- This is a focused regression pass, not sign-off on all permissions, StoreHub source-data accuracy, receiving, counts, transfers, or end-to-end stock workflows. Repeat the original QA scenarios with dedicated accounts and reconcile real reports read-only. Any tests that mutate business data, invoke a real sync, generate orders, or submit transfers must use an isolated environment.
- AI tests use deterministic fake providers and grounded context; production model output quality requires a separate read-only acceptance check.
