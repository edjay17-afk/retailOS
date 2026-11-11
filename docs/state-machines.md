# RetailOS state-machine inventory

Reviewed against the working tree on 2026-11-11, including existing local changes.
This inventories business lifecycles, derived states, background processes and UI
interactions. State names below are stored values where quoted; other names describe
implicit states. Arrows summarize paths, not an exhaustive authorization matrix.
External integrations and production data were not exercised.

## Decision

Keep state ownership local to each domain. Do not introduce a framework, database
enum migration, or one global state machine. Extract a transition contract when a
workflow is changed, preserving its existing public strings and side effects. Share
that contract between service validation, UI capabilities and agent tools. Permission,
location scope, input validity and freshness remain independent guards; a status by
itself never authorizes an operation.

This improves UX through truthful progress and recovery, DX through discoverable
transitions and focused tests, and AX through explicit state and action contracts.
It avoids changing operational policy merely to make a graph look tidy.

The implemented runtime change is deliberately bounded: transaction drawer requests
now have observable `idle`, `loading`, `ready`, `error` states and `aria-busy`.
Only the current request in an open drawer may display results or navigate to the
fallback receipt. Closing invalidates the request. Existing fallback navigation stays.

## Business and durable state

| Machine / owner | State and transitions | Important boundary |
| --- | --- | --- |
| Recommendation — [replenishment.py](../app/services/replenishment.py), [replenishment_sheets.py](../app/services/replenishment_sheets.py) | `pending` / `needs_review` → `approved` / `modified` / `rejected`; approved decisions become `ordered`; stale undecided suggestions become `expired`; recalculation can revive eligible expired rows | Review notes, reasons, permission, freshness, batch/sheet links and approved quantity all affect eligibility. Status alone is insufficient. |
| Replenishment run — same service | Created → `ok` or `blocked` | A blocked run is a freshness decision, not a transport failure. |
| Recommendation outcome — same service | Unmeasured → `insufficient_history` or `measured` | Separate from ordering lifecycle. |
| Order batch — [replenishment.py](../app/services/replenishment.py), [transfers.py](../app/services/transfers.py) | `approved` → `entered`; supplier batch → `received` on matching PO; transfer batch → `dispatched` → `received` | `dispatched` is written by code but missing from the model status comment. `cancelled` is documented in the model; do not infer a cancellation command from that alone. |
| Transfer sheet — [transfer_sheets.py](../app/services/transfer_sheets.py) | `draft` → `submitted` → `in_storehub`; reference can be updated; draft discard can unlink recommendations and return them to `pending` | Saved local dispatch list and external transfer confirmation are distinct. Model also mentions `cancelled`; inspect actual writers before exposing it. |
| PO draft — [po_drafts.py](../app/services/po_drafts.py) | `draft` → `saved`; draft editing/deletion before finalization | Export creation and local save do not prove a StoreHub order exists. |
| New product list — [new_product_lists.py](../app/services/new_product_lists.py) | `draft` → `saved` | List state is independent of each product's push outcome. |
| Product push — [product_push.py](../app/services/product_push.py) | `pending` / `failed` → `created` or `failed`; missing price leaves item pending | External write; barcode lookup recovers some ambiguous outcomes. Per-item commits preserve partial progress. Do not blindly replay create requests. |
| New product Lark mirror — [new_products.py](../app/services/new_products.py) | `recorded` → `lark_sent` / `lark_failed`, retry after failure | Mirror delivery is not product creation in the POS. |
| Receiving — [receiving.py](../app/services/receiving.py) | `open` → `completed` or `mismatch` | Physical count, external PO completion and inventory-event reconciliation are three different facts. |
| Cycle count — [counts.py](../app/services/counts.py) | `open` → `submitted` → `approved` | At least one count and variance explanations required; approval/export and later reconciliation are separate. Deletion is a separate lifecycle operation. |
| Cycle-count line — same service | Uncounted (`None`) → counted; variance and explanation determine submission eligibility | Zero is a valid count, not missing input. Scanned system snapshots have special preservation rules. |
| Inventory-event reconciliation — [inventory_events.py](../app/services/inventory_events.py) | `pending`, `matched`, `mismatch`, `needs_review`, `rejected`, `cancelled`; same-state resolution is a no-op | Resolver validates membership, not an edge graph. Terminal-looking labels are not currently enforced as irreversible. `reconciled_at` is derived from the new state. |
| Stock-event verification — [stock_events.py](../app/services/stock_events.py) | `verified_at=None` → verified by sync or already-recorded import | Verification can close an associated issue; keep that system path distinct from human verification. |
| Store transfer receipt — [transfers.py](../app/services/transfers.py) | No receipt → counted confirmation with zero or more differences | Unique confirmation and destination access matter. External status does not establish store receipt. |
| Issue — [issues.py](../app/services/issues.py) | `open` → `acknowledged` → `in_progress` → `resolved` → `closed`; early resolve and manager reopen also allowed | Existing service permits repeated resolve and reopening nonclosed issues; acknowledge/start can silently no-op. UI offers a narrower set. Preserve or deliberately change these semantics with tests, not a generic enum. |
| Issue SLA — same service | Escalation level 0 → 1 for missing acknowledgement → 2 for overdue; manual increments also exist | Orthogonal to issue status; timestamps and notification dedupe affect behavior. Reopening does not reset all prior metadata. |
| Inspection — [inspections.py](../app/services/inspections.py), [lark_inspections.py](../app/services/lark_inspections.py) | `pending` → `in_progress`; overdue → `missed`; completion → `completed` or `late`; imported report → `submitted` | A missed task may still be worked; external submission must not invent a local completion actor/time. |
| Inspection answer/evidence — same services | Unanswered → pass/fail with required note/evidence for failures | Item completion, media upload and inspection completion are separate. |
| Expiry batch — [expiry.py](../app/services/expiry.py) | `active`, `cleared`, `written_off`; date produces urgency bands | Current setter accepts these labels without a transition graph. Do not assume write-off updated POS stock. |
| Data-quality finding — [dq.py](../app/services/dq.py) | Absent → active; `resolved_at` set when finding disappears; cleared on recurrence | Finding dedupe and linked critical issue have separate lifecycles. |
| Permit — [compliance.py](../app/services/compliance.py) | Model declares `active`, `renewed`, `retired`; implemented renewal updates dates in place | Reminder thresholds are clock-driven derived states. A renewal does not currently transition status to `renewed`. |
| Equipment maintenance — same service | Equipment is a compliance-item kind with the same date-based warning stages | Uses the compliance lifecycle, not a separate equipment-status enum. |
| Notification — [notifications.py](../app/services/notifications.py) | Absent → unread → read, with dedupe on creation | Role notification read status is shared across recipients today. Changing to per-user reads requires a data-model change. |
| Order-document overlay — [order_documents.py](../app/services/order_documents.py) | Unsaved source → saved revision; source hash can become changed; revision conflict rejects stale save | Already uses optimistic concurrency. Overlay edits must not mutate external source data. |

## External mirrors, identity and jobs

| Machine / owner | State and transitions | Important boundary |
| --- | --- | --- |
| Purchase-order mirror — [storehub/sync.py](../app/storehub/sync.py), [purchasing.py](../app/services/purchasing.py) | External statuses including `open`, `partiallyReceived`, `completed`; missing flag; derived `incoming`, `stale`, `closed`, `deleted` | External status and local classification are separate. Stale POs remain visible but do not count as incoming. |
| Imported transfer — [stock_transfers.py](../app/services/stock_transfers.py) | `Created`, `Shipped`, `Completed`, `Cancelled`; import preview → confirmation → persisted snapshot | Rank/timestamp/hash checks reject older or conflicting terminal snapshots. Do not lowercase into a different workflow. |
| Transfer inbox — [transfer_inbox.py](../app/services/transfer_inbox.py) | File waiting → processed / duplicate / failed, represented by folders and import timestamps | File movement, database import and freshness notification are separate effects. |
| Loyverse stock edit — [loyverse/stock_edit.py](../app/loyverse/stock_edit.py) | Proposed old/new quantities → re-read → changed / skipped conflict / error | Live external write, distinct from StoreHub's physical-event capture; compare observed stock before applying. |
| 7-Eleven imports and suggestions — [seven_eleven.py](../app/services/seven_eleven.py), [seven_replenishment.py](../app/services/seven_replenishment.py) | Imported reports/slips and duplicate detection; derived suggestion flags for trend, waste and sparse history | Sales-only calculation, not the persisted Recommendation approval machine. |
| Sales/receipt mirror — [storehub/sync.py](../app/storehub/sync.py), [loyverse/sync.py](../app/loyverse/sync.py) | Sale/refund/cancelled, imported/upserted | Financial classification is external truth, not a local editable lifecycle. |
| Shift/Z-reading detail — [sales_shifts.py](../app/services/sales_shifts.py), [zreadings.py](../app/services/zreadings.py), [zreceipts.py](../app/services/zreceipts.py) | Summary known → detail fetched (`detail_ok`); report status and completeness vary by source | Summary presence does not prove complete detail; retries/backfills are separate. |
| Product/location availability — [models.py](../app/models.py), sync services | Product present/missing, tracked/untracked; location monitored/unmonitored and open/closed; replenish enabled/disabled | Several independent axes, not one product/store status. Historical rows remain usable. |
| User and session — [security.py](../app/security.py), [routes_core.py](../app/web/routes_core.py), [routes_admin.py](../app/web/routes_admin.py) | Anonymous → authenticated; failed attempts → lockout; password-change-required → normal; active → inactive/deleted; session → expired/revoked | Role and location scope are guards, not workflow states. Logout/deletion must revoke sessions. |
| Sync run — [storehub/sync.py](../app/storehub/sync.py), [loyverse/sync.py](../app/loyverse/sync.py) | `running` → `ok`, `partial`, `failed` | Run outcome and domain freshness are independent; prior successful data may remain usable. |
| Domain freshness — [freshness.py](../app/services/freshness.py) | `never` → `fresh` → `stale` with clock; successful sync → `fresh` | Last error/consecutive failures do not replace last-success age. |
| Enrichment/read cursors — [po_prices.py](../app/services/po_prices.py), [stock_movements.py](../app/services/stock_movements.py) | Pending exact prices or newer inventory changes → read attempt → enriched/read-through; last-attempt and last-success timestamps control retry/catch-up | Missing detail is not zero; a failed attempt must not falsely advance completeness. |
| Source scope — [sources.py](../app/services/sources.py), [web/deps.py](../app/web/deps.py) | Selected all/source/account → normalized authorized request scope | Session-scoped query criteria and persisted selection are independent from authorization; do not let an old request scope restrict calculation of real access. |
| Stock estimate — [stock_estimate.py](../app/services/stock_estimate.py) | Derived adjustment changes with PO and transfer lifecycle; closed stores receive no adjustment | `Created` transfers subtract outgoing stock; `Shipped` already left. Estimate is not a persisted stock ledger. |
| API retry/authentication — [storehub/client.py](../app/storehub/client.py), [loyverse/client.py](../app/loyverse/client.py), [robot.py](../app/robot.py) | Ready → request → success / retry / auth failure; throttling/backoff and session renewal | External writes need operation-specific replay rules. Authentication and transient transport errors differ. |
| Scheduled job — [jobs.py](../app/jobs.py) | Scheduled → executing → success/failure in process-local `LAST` | Process memory is not durable job history. |
| Run-now orchestration — [run_now.py](../app/services/run_now.py) | Run registered; each step `waiting` → `running` → `ok`/`failed`; aggregate `done`/`ok` | One active run is reused; tokens are process-local. Restart loses polling identity. |
| Lark mirror — [lark.py](../app/lark.py), [wastage_lark.py](../app/services/wastage_lark.py) | Unlinked → linked; content hash unchanged/changed → skip/update; failure → later retry | Mirror identity/hash is an implicit delivery state. Wastage import and mirror are different directions. |
| Backup — [backup.py](../app/services/backup.py) | Writing → integrity verified or removed on failed verification → retained/pruned | File existence alone is not proof of a verified backup. |
| Schema migration — [dbinit.py](../app/dbinit.py) | Version absent → applied and recorded | Migration state belongs to schema versioning, not a business workflow library. |
| Audit chain — [audit.py](../app/audit.py) | Append-only event extends previous hash; verification succeeds/fails | An integrity protocol rather than an editable status field. |
| AI output/provider — [ai/service.py](../app/ai/service.py), [ai/provider.py](../app/ai/provider.py), [ai/validate.py](../app/ai/validate.py) | Request → provider/fallback → `ok`/`failed`; validation records supported/unsupported claims | Generation success does not imply grounded output or authorization. |
| AI action — [ai/service.py](../app/ai/service.py), [ai/policy.py](../app/ai/policy.py) | `proposed` / `needs_approval` → `executed`, `failed`, `denied`, `rejected` | Gate is checked again at execution; nested transaction rolls back a failed tool. UI approval is not permission bypass. |
| AI conversation — [ai/conversations.py](../app/ai/conversations.py) | New → continued through parent/output linkage → resumed | Ownership and context scope must survive resume; conversation identity is distinct from pending generation. |

## Browser state

These machines also have server-side counterparts above. A dismissed dialog or
aborted fetch does not prove a server-side write was cancelled.

| Machine | Evidence / state |
| --- | --- |
| Draft persistence | [draft_feedback.js](../app/web/static/draft_feedback.js): clean/dirty, saving, saved, definite failure, uncertain outcome, leaving. Uncertain saves require reload before replay. |
| Transaction drawer | [tx_drawer.js](../app/web/static/tx_drawer.js), [app.js](../app/web/static/app.js): closed/idle → loading → ready/error; supersede/close invalidates results. Shared dialog focus and close behavior live in app.js. |
| Assistant | [assistant_chat.js](../app/web/static/assistant_chat.js): closed/open, idle/generating/aborting, history loading, new/resumed conversation, context enabled/disabled/loading; request sequence suppresses stale context success. |
| Run-now panel | [sync_run.js](../app/web/static/sync_run.js): idle/start/polling/finished/failed/connection lost; modal visibility is independent of job execution. |
| Count scanner | [count_scan.js](../app/web/static/count_scan.js): searching/results/selected, busy, dirty, saving; sequence guards and keyboard active-option state. |
| Keyboard scanner | [scanner.js](../app/web/static/scanner.js): idle/buffering/timing/classified as scan/submitting/reset; timing and minimum length distinguish typing. |
| Camera scanner | [scan.js](../app/web/static/scan.js): opening/permission pending/decoding/found/error/closed, native detector or ZXing fallback; camera stream has its own cleanup lifecycle. |
| PO/transfer/replenishment editing | [replenishment_sheet.js](../app/web/static/replenishment_sheet.js), [replenishment.js](../app/web/static/replenishment.js), [po_draft.html](../app/web/templates/po_draft.html), [transfer_sheet.html](../app/web/templates/transfer_sheet.html) | 
| ↳ Editing submachines | Product lookup, selection, quantity/price validity, changed lines, selection sets, submission/proceed, persisted draft. Inline template JavaScript is included in the scan. |
| Document editor/export | [documents.js](../app/web/static/documents.js): editing/saving/revision conflict. [export_buttons.js](../app/web/static/export_buttons.js) temporarily sets a form export field and submits; it does not track download success. |
| Stock move / wastage lookup | [stock_move.js](../app/web/static/stock_move.js), [wastage_lookup.js](../app/web/static/wastage_lookup.js): lookup pending/selected/missing/error; line selection and submission. |
| Live dashboard | [dashboard_live.js](../app/web/static/dashboard_live.js), [dashboard_refresh.js](../app/web/static/dashboard_refresh.js), [dashboard_b.js](../app/web/static/dashboard_b.js): refreshing/success/failure, visibility/poll timers, retained last content. |
| Inspection/media interactions | [inspection_lark.js](../app/web/static/inspection_lark.js), [app.js](../app/web/static/app.js), [inspection_form.html](../app/web/templates/inspection_form.html): answer progress, media selection/preview, submit/busy/error; external report availability. |
| Navigation and responsive shells | [app.js](../app/web/static/app.js), [phone_app.js](../app/web/static/phone_app.js), [mobile.js](../app/web/static/mobile.js), [sidebar_fly.js](../app/web/static/sidebar_fly.js), [navigation_motion.js](../app/web/static/navigation_motion.js): menu open/closed, viewport mode, flyout/focus, navigating/restored. |
| Data-warning poller | [data_warning.js](../app/web/static/data_warning.js): waiting/busy/stopped on authentication failure; visibility controls polling and failures retain prior content. |
| Small controls | [account_menu.js](../app/web/static/account_menu.js), [tabs.js](../app/web/static/tabs.js), [daterange.js](../app/web/static/daterange.js), [expiry_dialog.js](../app/web/static/expiry_dialog.js), [login_extras.js](../app/web/static/login_extras.js), [crumbs.js](../app/web/static/crumbs.js): expanded/selected/custom-date-valid/dialog-open/password-visible and persisted preferences. |

## Risks and next changes, in priority order

1. **Async ownership:** fixed the reproduced transaction drawer stale-failure path.
   Apply the same current-request rule to both success and error callbacks when
   modifying other fetch-driven screens. Test close, supersede, retry and session expiry.
2. **Concurrent commands:** many services check then mutate ORM status. Sequential
   duplicate-submit tests do not establish cross-session safety. Introduce atomic
   compare-and-set or durable operation identities for approval, receipt confirmation
   and external writes only after characterizing each workflow's side effects.
3. **Run-now lifetime:** process-local state and interval polling deserve recovery
   semantics. Connection loss should mean “outcome unknown,” not “job failed.” Test
   restart and out-of-order polls before changing the job model.
4. **Issue contract:** reconcile the narrower UI with service no-ops/reopen semantics.
   Include system auto-close writers in inspections, stock verification and migrations;
   routing everything through the human close command would break those workflows.
5. **Model/document drift:** repair stale comments only after confirming all writers.
   The actual code already includes count reconciliation and direct product pushes;
   older prose saying otherwise is not authoritative.

## Repeatable evidence and coverage limits

Run from the repository root:

```powershell
.venv\Scripts\python scripts/inventory_state_machines.py --output output/state-machine-evidence.json
.venv\Scripts\python -m pytest tests/test_tx_drawer_state.py tests/test_tx_panel.py -q
```

The JSON includes every scanned first-party Python, JavaScript and HTML file, plus
line-level candidate evidence and Python owner names. It does not import the app,
read credentials, touch a database, or contact integrations. It deliberately includes
false positives (for example HTTP status) so a future agent can inspect the source.
CSS, vendor code, generated previews, external services and live production state
are outside this scan. Boolean, timestamp, row-existence, hash and file lifecycles
can evade any naming-based scan; this is a reviewed inventory plus discovery aid,
not a mathematical guarantee that every possible runtime state has been found.

For any future extraction, record: owner, states, events, source/target edges, guards,
effects, idempotency behavior, concurrency behavior, recovery, and UI/agent-visible
capabilities. Keep tests for invalid and duplicate commands as well as the happy path.

## Verification for this change

- Static scan completed: 207 first-party source/template files, 1,761 candidate
  evidence lines. All relative source links in this document resolve.
- Six targeted tests passed: five browser cases covering superseded success/failure,
  closed-drawer success/failure, and current-request fallback; plus the existing
  transaction-panel endpoint test.
- The broad baseline run was interrupted and is not a full-suite pass. Three failures
  were reproduced separately in `test_chat_charts.py` and `test_custom_range_check.py`:
  reversed date ranges return 400 where tests expect 200. These paths are unchanged
  by this work. Existing user edits have been preserved.
