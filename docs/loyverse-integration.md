# Loyverse sales-only replenishment

Open Auto replenishment > Loyverse. Admin and management can access this page; store staff access awaits explicit store-to-user assignments.

Three accounts are read from LOYVERSE_TOKENS_FILE. Tokens remain in the external file and are never stored in the application database or rendered. Only GET requests are used; nothing is written to Loyverse. Do not commit token files.

Sales sync runs every minute while the server is running, with catalog refresh every 15 minutes. Initial import covers at least 16 days, extended automatically if the shared lookback increases. Each account commits only after all requested pages have been read. Overlapping incremental imports update existing receipts instead of duplicating them. A failed account retains previous data and displays its error.

Loyverse accounts, stores and item variants are separate from StoreHub. The suggestion is net units sold divided by store days with sales, multiplied by the configured sales-only cover days. Completed Philippine business days only; refunds are deducted, cancelled receipts excluded. Whole items round up to whole units; weighted items round up to 0.001. Items with no positive net sales do not receive suggestions. Short history is flagged. Missing/deleted items and failed or stale (15-minute) imports do not produce actionable quantities.

Suggestions are for items sold, not their recipe ingredients. Stock, open orders, StoreHub sales, case packs and minimum order quantities do not affect these quantities. This release provides demand suggestions and filtered CSV export; it does not create transfer sheets or purchase orders. Shared lookback/cover settings are in Settings.

Validation: tests/test_loyverse.py uses a temporary database and fake API; tests idempotency, account isolation, refunds, cancellations, today exclusion, failure retention, timezone conversion, pagination, rate-limit retry, sanitized authentication errors, filtered exports, staff denial and desktop/mobile overflow. No business data is changed by these tests.


## Shared Retail OS pages

The POS source/account selector scopes Dashboard, Sales, Sales by product, Transactions, Stock, Employees and local operational pages. StoreHub remains the initial selection; All POS sources combines only PHP accounts. Source selection persists between pages and filters CSV exports and AI data access. Store/date filters remain within a source; changing source clears incompatible IDs but retains date parameters.

Shared identities explicitly record source, account and external ID. Legacy StoreHub ID columns contain namespaced compatibility keys for Loyverse; these keys are never sent to the StoreHub connector. Products with matching names or barcodes are not merged across accounts. StoreHub product sync cannot deactivate Loyverse products. Deleted items remain in historical receipts.

The expanded importer reads receipts (amounts, tax, discounts, line items and payment-method totals), catalog, categories, suppliers, employees and inventory. It excludes customer profiles, receipt notes and payment/card details. Receipt totals include taxes and any tips/surcharges; item reports exclude receipt-only charges. PHP currency is checked before import. Inventory is a snapshot, not historical movement reasons.

Initial history requests 90 days. If the receipts endpoint rejects that window with HTTP 402, the importer retries 30 days and retains that supported window for incremental imports. Actual history start is displayed. Earlier periods are not represented as complete. Catalog refresh is every 15 minutes; sales/inventory jobs run every minute with overlap protection and retries. A failed import retains previous data and shows a freshness warning.

Admin/Management can view all authorized stores. Other roles must be explicitly assigned Loyverse locations in Settings > Users; existing unassigned users do not gain access to the new accounts automatically. Employees imported from POS are not login accounts.

Stock counts can read live Loyverse quantities. Wastage, expiry batches, stock changes, issues and inspections are local records. Cross-account product/location combinations are rejected. They do not write back to Loyverse. The existing StoreHub-specific Lark catalog/staff/inventory/inspection/wastage/expiry mirrors are not expanded to these accounts without mapping their destination tables.

Purchase-order, stock-transfer and attendance/timesheet synchronization is unavailable in the documented Loyverse API. StoreHub export workflows reject Loyverse IDs. Incoming quantities are labelled unavailable. Register shifts are not staff attendance. Loyverse replenishment remains sales-only and does not create transfers automatically.

Rollout backup: data/backups/pre-loyverse-system-20261024-080044.db. Rollback requires stopping the app and restoring a matching pre-change application/database pair; restoring this backup would discard later local records, so take a current backup first. The schema migration only adds columns and keeps existing StoreHub records.


## Rollout verification (24 October 2026)

All three accounts imported successfully: 12 stores, 19,384 receipt records at the initial checkpoint, 375 current/historical product records, 3 employee records returned by the API, and 2,582 inventory levels. Account 1 history starts 26 July 2026 (90 days); accounts 2 and 3 start 24 September 2026 (30 days after the API rejected the 90-day window). Receipts continue accumulating locally after the initial import.

Per-account net receipt totals reconciled to daily reporting totals with PHP 0.00 difference. This verifies the imported receipt-to-report calculations; it is not a substitute for the user's independent Loyverse back-office reconciliation. The final combined regression command passed 110 tests across web routes, shared Loyverse imports, AI, Lark isolation, stock scanning, StoreHub sync, and migrations. Additional runs covered translations, employees, mobile layout, and sales export behavior. No production accounts or business records were created for QA; real synchronization was the authorized rollout.

Expanded reporting sync is enabled through LOYVERSE_REPORTING_ENABLED=true. Manual verification: choose Loyverse or an account in POS source/account; open Sales, Sales by product, Transactions and Stock; select a store/date; export CSV; open a product and return; verify the selected scope remains; assign a dedicated test user to one store in an isolated environment and confirm other stores/details/exports are denied.
