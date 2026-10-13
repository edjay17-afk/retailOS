# Retail OS: store and warehouse operations on top of StoreHub

A local web system for The Daily Centro. It copies sales, stock, products, purchase orders and timesheets from StoreHub, checks the data,
suggests orders, and runs the daily operation (inspections, issues, receiving, counts, expiry) with a full audit trail.

* **StoreHub stays the source of truth** for sales, prices, stock and purchase orders. This system only *reads* StoreHub (HTTP GET). It cannot change
  StoreHub; anything that must change there (receiving, transfers, write-offs, entering a PO) is done by a person in the StoreHub back office,
  from a list this system prepares. The next sync verifies it.
* **Everything else is recorded here**: inspections, issues, approvals, counts, expiry dates, decisions, and an append-only audit log.
* **All business logic is code** (calculations, thresholds, approvals, permissions), unit-tested. The formulas are explainable. An **AI assistant** (see below) explains and proposes, but a person decides.
* Optional **Lark mirror** copies the records into a Lark Base automatically (see below).

## Run it

```
C:\RetailOS\start.bat                 (opens the system on http://127.0.0.1:8000)
```

First time only:

```
.venv\Scripts\python -m app.cli create-admin --username admin     (prints a one-time password; you must change it at first sign-in)
.venv\Scripts\python -m app.cli sync                              (optional: loads all StoreHub data now; the scheduler also does it on start)
```

Start automatically with Windows (run once in PowerShell as yourself): `powershell -ExecutionPolicy Bypass -File C:\RetailOS\scripts\install_autostart.ps1`

Phones in the store network: set `HOST=0.0.0.0` in `.env`, restart, allow port 8000 in Windows Firewall for the private network only, and open
`http://<this computer's IP>:8000`. Traffic is plain HTTP, so keep it on the store network. For wider access put it behind an HTTPS reverse proxy.

## What it does

| Area | What happens |
|---|---|
| **Sync** | Sales every 5 min, stock and POs every 10, timesheets every 20, products daily, people every 6 h. A nightly reconciliation re-reads the last 7 days from StoreHub and compares every day and store. Never more than 2 calls a second (StoreHub blocks above 3). Failed syncs retry with back-off, mark the data stale, and raise an issue. |
| **Freshness** | Every screen shows how old its data is. When sales, stock or PO data is stale, replenishment pauses instead of using old numbers. |
| **Stock history** | StoreHub keeps none, so every change of quantity seen between syncs is logged, plus a daily snapshot. |
| **Data quality** | Negative stock, products with no supplier or cost, stale open POs, sales spikes, PO received but stock unchanged, reconciliation mismatches. Each is a finding that clears itself when fixed; critical ones open an issue. |
| **Replenishment V1** | Stores are topped up from the warehouse; the warehouse is ordered from suppliers using the stores' combined demand. `ADS x (lead + review) + safety`, minus on hand and incoming, rounded to case pack, MOQ and a cover cap. Every suggestion shows its working and a confidence level. Manager approves, modifies or rejects with a reason. Approved lines become order batches (CSV) to key into StoreHub; the PO number is saved and tracked. |
| **Purchase orders** | An open PO more than 7 days past its date is "stale": shown, flagged, and not counted as incoming. Deleted POs are detected. |
| **Receiving** | Count against the PO (quantity, damaged, expiry). Differences open an issue. Expiry dates are recorded for tracking. After counting, receive the PO in StoreHub; the sync verifies it. |
| **Inspections** | Store (opening, midday, closing) and warehouse checklists are created every day. Staff must be clocked in (StoreHub timesheet). A failed item needs a note and a photo, and opens an issue. Late checklists alert the manager. |
| **Issues** | Severity P0 to P3, owner and due time always set, evidence photos, escalation if not acknowledged or overdue, and a manager must verify the fix before closing. |
| **Counts** | Targeted cycle counts (negative stock first). Approved differences produce a stock take file for StoreHub's import. |
| **AI assistant** | Daily briefing, questions and answers, and "explain this suggestion", on the **Assistant** page. It only sees numbered facts written by the system; every statement is typed (fact, calculation, maybe, advice) and must cite its facts; numbers not found in the cited facts are flagged. It can only *propose* actions (create issue, assign, remind, prepare a count list, approve a suggestion); a policy gate checks permissions, order limits, store access and data freshness, and high-risk actions always need a person. Every answer is stored with what it was given, so it can be audited. |
| **Transfers** | Approved store transfers are keyed into StoreHub, dispatched by the warehouse, then counted by the store; a difference opens an issue. |
| **Stock transfers** | The Stock Transfers page imports StoreHub's detailed stock-transfer CSV with a preview and confirmation. Transfers are matched by S.T ID, repeated imports do not duplicate records, and older lifecycle snapshots are skipped. Warehouse, management and admin can import; staff and store managers can view transfers within their location access. The original export and import audit are retained. Export status is preserved and amounts are displayed in PHP; importing does not change inventory or confirm physical receiving. English and Chinese are supported. |
| **Permits and maintenance** | Permits, certificates and equipment service dates with 30, 14 and 7 day and overdue alerts. |
| **Performance** | Order acceptance, stock accuracy, dead stock, issue response times, checklist completion, receiving differences, data health. |
| **Brief** | The home page lists only what needs attention, each item with its facts and calculation. |
| **Security** | Server-side permissions for 5 roles and per-location scope, CSRF tokens, hashed passwords, lockout, validated photo uploads, security headers, append-only hash-chained audit log. |

## The AI assistant

It works out of the box if Claude is installed and signed in on this computer (`claude` command). For a faster, cheaper and sturdier connection add
`ANTHROPIC_API_KEY=...` to `.env` and restart. A briefing takes about 20 seconds and costs a few US cents; the daily cap is a setting (`ai.max_calls_per_day`, default 60).

You can use Google Gemini instead: set `AI_PROVIDER=gemini` and `GEMINI_API_KEY=...` (a billed key also allows `GEMINI_MODEL=gemini-pro-latest`). A local Ollama model (`OLLAMA_MODEL`, default `qwen2.5:7b`) is the automatic backup: when the main model is down, overloaded or over quota, the local one answers, so nothing leaves the computer in that case. Set `OLLAMA_FALLBACK=false` to turn the backup off, or `AI_PROVIDER=ollama` to use only the local model. The local model is private and free but gives shorter, less careful answers; every statement is still checked against the data.
If no model is connected the AI screens say so and everything else keeps working. `ai.auto_low_risk` (off) would let it run low-risk actions itself; high-risk actions never run automatically.
How well it is doing is measured on **Assistant, How well is it doing**: usefulness ratings, unsupported-claim rate and action approval rate.

## Roles

Staff, Store manager, Warehouse, Management, Admin. What each can do is the table in `app/security.py` (`PERMS`); it is enforced on every request.
A store manager can approve an order line up to the limit in Settings (default 20,000); above it, management must approve.

## First things to do after the first start

1. Sign in as `admin`, change the password (at least 8 characters; letters, numbers and special characters are all allowed), and create the real users (Users page). Link staff to their StoreHub employee so the clock-in check works.
2. **Settings**: confirm which locations are monitored (the four Daily Centro locations are on by default), set each store's manager.
3. **Suppliers**: names, lead times, review periods, minimum order. Use **Add a supplier** for a supplier absent from the list, then assign it to products in Product set-up. Manually added suppliers have a `LOCAL-` code for CSV name imports. Until a lead time is set, the engine assumes 3 days and lowers its confidence.
4. **Product set-up**: confirm suppliers inferred from PO history, and fill case pack and MOQ (export CSV, edit, import).

Admins can use **Settings → Users → Delete user** to remove an account from the users list and revoke all its sessions. Past operational records and audit references are retained. Your own account and the last active admin cannot be deleted.
5. Count the negative-stock items (Counts page). Replenishment is only as good as stock accuracy.
6. Decide the rule for old open POs (Settings, `po.stale_days`) with purchasing.

## Data and backups

Everything is in `C:\RetailOS\data`: `retailos.db` (SQLite), `uploads` (photos), `backups`, `logs`. A verified backup is written at 01:45 and 13:45 and kept
for 40 copies; **also copy the `data` folder to another disk or cloud regularly**. Restore: stop the app, copy a backup over `retailos.db`, start.
`python -m app.cli verify-audit` checks the audit log has not been altered.

## Lark mirror (optional)

Create an empty Base in Lark, add your Lark app as a collaborator with manage access, then put `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_BASE_TOKEN` in `.env`
and run `python -m app.cli lark-setup` once. From then on issues, daily sales, replenishment decisions, inspections, receiving and expiry batches are
copied automatically every 5 minutes. It is one-way and idempotent; Lark is storage, never a second source of truth.

## Settings in `.env`

`STOREHUB_USERNAME`, `STOREHUB_TOKEN` (never share or commit this file), `HOST`, `PORT`, `SECRET_KEY`, `DATA_DIR`. `STOREHUB_RPS` cannot exceed 2.5.

## Develop and test

```
.venv\Scripts\python -m pytest tests -q        (106 tests: formulas, sync, idempotency, permissions, workflows, AI grounding and policy gate, migrations, Lark mirror)
```

The tests use a fake StoreHub; nothing real is touched. Layout: `app/storehub` (client, sync), `app/services` (business rules), `app/web` (screens),
`app/ai` (assistant: context, validation, tools, policy gate), `app/jobs.py` (schedule), `app/lark.py` (mirror), `app/models.py` (data model), `app/dbinit.py` (schema versions and migrations).

See `docs/SPEC_COMPLIANCE.md` for exactly what is and is not built against the V2 spec and the proposal.

## Known limits

* StoreHub's API (guide v1.7, 2019) has no stock writes, PO creation, supplier data or webhooks, so those stay manual and data is polled.
* StoreHub has no expiry, case pack, lead time or supplier per product; they are set up here.
* The stock take file follows StoreHub's documented export (product, SKU, category, quantity); check its headers against the template your back office gives you.
* Returns have not occurred yet in your data; the logic is tested but has not met a real return.
* SQLite serves one store group comfortably; if you outgrow it, the data layer (SQLAlchemy) moves to PostgreSQL with a configuration change and a data copy.
