# Compliance with CONVENIENCE_STORE_SYSTEM_SPEC_V2.md and the proposal PDF

Status: **Done**, **Partly** (what is missing is stated), **Not built** (and why). Nothing here is claimed that is not tested; see `tests/`.

## Spec V2

| Spec | Requirement | Status | Where / note |
|---|---|---|---|
| 2.1 | StoreHub is the source of truth; no second inventory | Done | Stock is a read-only copy overwritten each sync (`storehub/sync.py`). The system never writes to StoreHub. |
| 2.2 | Business logic in the middleware, not in Lark | Done | All rules are Python in `app/services`. Lark only receives a mirror (`app/lark.py`). |
| 2.3 | Lark replaceable by a custom app | Done | The custom web app is the working layer; Lark is optional storage. |
| 2.4 | Deterministic core, AI on top; AI never invents numbers | Done | Numbers come from code. AI facts are written by code; every AI statement is re-checked (`ai/validate.py`). |
| 2.5 | Human approval first | Done | Policy gate (`ai/policy.py`); orders, write-offs, price changes and supplier orders always need a person. |
| 2.6 / 18 | Auditable, append-only log | Done | Hash-chained, trigger-protected `audit_event`, includes AI calls (`actor_type = ai`). |
| 3 | Layers: ingestion, ground truth, rules, event engine, forecast, AI, tools | Partly | Ingestion, ground-truth DB, rule engine, AI layer and tool layer are built. The **event engine** is the data-quality and alert rules (no separate event bus). The **forecast layer** is not built: the spec says no black-box forecasting in the MVP. |
| 4 / 26 | Open technical questions and programmer checklist | Done | Answered in `Programmer_Technical_Review_Answers.pdf`; the answers that could be tested were tested on the live account. |
| 5 | Canonical data model | Partly | All entities exist with the same meaning; some names differ. Fields StoreHub does not have (supplier, lead time, case pack, MOQ, expiry) are owned here. `subcategory` exists but StoreHub gives none and there is no screen for it. |
| 6 | Sync: schedule, logs, failure policy, idempotency | Done | Sales 5 min, stock and POs 10, products daily, timesheets 20, nightly reconciliation; retries, stale marking, issue on failure; upserts on StoreHub keys. |
| 7 | Data quality layer | Done | 12 rules plus reconciliation; each finding self-resolves; critical ones open an issue. |
| 8 | Replenishment V1 | Done | ADS, target stock, position, case pack, MOQ, max-stock cap, explanation, approve / modify / reject with reasons, safety rules. Checked against an independent calculation from raw StoreHub data: 191 of 191 products identical. |
| 9 | Receiving, expiry, cycle counts | Done | Receiving vs PO with issues; expiry captured at receiving with 30/14/7/3-day warnings; targeted cycle counts. |
| 10 | Inspection and issue engine | Done | Lifecycle, P0 to P3, escalation, verify before close. |
| 11 | Cash variance | Done | Counted minus expected; thresholds are settings; repeat and large rules. |
| 12.2 V2.0 | AI-assisted: summary, prioritisation, explanation, root-cause hypotheses, cross-store comparison, next steps, Q&A | Done | `/assistant`: daily briefing, questions, "explain this suggestion". Hypotheses are labelled "Maybe". |
| 12.2 V2.1 | Agent-assisted: create issues, assign, draft, approval requests, reminders | Partly | Create issue, assign, send reminder, prepare a count list, approve a suggestion (with approval). **Not built:** drafting supplier-order messages and automatic follow-up of unresolved tasks. |
| 12.2 later | Autonomous stages | Not built, by design | A switch exists (`ai.auto_low_risk`) for low-risk actions only; it is off. High-risk actions can never run automatically. |
| 12.3 | Structured context package | Done | Numbered facts by section: freshness, sales, inventory, replenishment, purchase orders, operations, data quality, decision history, policy (`ai/context.py`). |
| 12.4 / 12.5 | Structured output; fact / calculation / hypothesis / recommendation | Done | Every statement is typed and cited; unsupported numbers are flagged on screen. |
| 13 | Tool classes and policy gate | Done | Read / low-risk / high-risk tools; gate checks permission, risk, amount, store scope, freshness, confidence. |
| 14 | Decision memory | Done | Suggestion, calculation, human decision, reason, outcome (stock-out check after 7 days) are stored and fed to the AI as context. Rules are never changed automatically. |
| 15 / 24 | Evaluation and metrics | Done | `/performance` (replenishment, inventory accuracy, dead stock, issues, inspections, receiving, cash, data health) and `/assistant/metrics` (usefulness, unsupported-claim rate, action approval and success). A "before roll-out" baseline comparison is not automated. |
| 16 | Daily management brief | Done | Deterministic brief on the home page and the AI briefing, both fact-first. |
| 17 | Roles, enforced on the server | Done | Staff, store manager, warehouse, management, admin, plus per-location scope. |
| 19 | Lark views for the MVP | Done | The eight MVP views are screens in the app; the optional mirror copies the records to Lark. |
| 20 | Three loops | Done | Store operations, replenishment, receiving (plus warehouse transfers). |
| 21 | Roadmap phases 0 to 7 | Phases 0 to 6 done | Phase 7 (controlled autonomy) is deliberately not enabled. |
| 22 / 23 | Definition of done and acceptance tests 1 to 7 | Done | Tests 1 to 7 are automated or were run on live data (`tests/`). |
| 27 | Rules for coding agents | Followed | Server-side authorisation, idempotent sync, audit events, deterministic tested calculations, documented deviations (this file). |

## Proposal PDF (flows F1 to F13, tables, checklists)

| Item | Status | Note |
|---|---|---|
| F1 Setup, F2 Product sync, F3 Stock sync, F4 Sales sync | Done | Products match by barcode first (only 134 of 1,429 products have a SKU). |
| F5 Replenishment with manager approval | Done | Suggestions become order batches to key into StoreHub; the PO number is saved and tracked. |
| F6 PO tracking and receiving | Done | Counts, damage, expiry dates and a delivery photo. |
| F7 Store inspections, F8 Warehouse inspection and cycle count | Done | Clock-in check against StoreHub timesheets. |
| F9 Nightly checks | Done | Cash, stock variance, low stock, expiry, reconciliation. |
| F10 Issue handling, F12 Missed checklist | Done | |
| F11 Permits and maintenance | Done | `/compliance`: 30, 14, 7 days and overdue alerts. |
| F13 API safeguards | Done | 2 calls a second maximum, retries, back-off, logs, stale marking. |
| Flow E Warehouse to store | Done | Approve, key in StoreHub, warehouse dispatch, store count, difference becomes an issue. |
| Alerts and approvals in Lark messages | Partly | In-app notifications always; critical alerts can also go to a Lark chat (`LARK_ALERT_CHAT_ID`). Approvals happen in the app, not in Lark. |
| Phase 5 "auto-send orders to suppliers" | Not built, by design | Both documents say not until recommendations are proven. StoreHub also cannot create purchase orders. |
| Manual steps in StoreHub back office | Remain | Receiving, transfers, write-offs and entering approved orders. Unavoidable: the StoreHub API has no write endpoints. |

## Known limits

* The StoreHub API guide is v1.7 (2019); behaviour was checked against the live account but not against newer documentation.
* The AI needs a model: it uses `ANTHROPIC_API_KEY` if set, otherwise the Claude command line on this computer. Without either, the AI screens explain that it is not connected and everything else works.
* Returns have not occurred in the data yet; that logic is unit-tested only.
