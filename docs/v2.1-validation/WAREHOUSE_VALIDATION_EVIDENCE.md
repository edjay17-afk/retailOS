# Retail OS V2.1 — Warehouse validation evidence

**Outcome: on-site validation incomplete; final freeze cannot yet be issued.**

Reviewed 2026-10-09T02:08:57.404197+00:00 (UTC). This report uses only the two user-supplied baselines, local code, isolated tests and a read-only local database snapshot. No physical warehouse flow was observed and no staff-confirmed notes or sign-off were supplied. Competitor research was not expanded. Original downloads are unchanged. The proposed specification changes only Section 16; its FREEZE CANDIDATE status, FC1 version and every other section remain unchanged.

## Evidence register

| ID | Class | Source | Finding / limit |
| --- | --- | --- | --- |
| E01 | SYSTEM CONFIRMED | app/services/receiving.py:20,50,84; app/web/templates/receiving.html:17 | PO-linked count/damage/variance and optional photo; no persisted reconciliation match state or required variance reason/photo. |
| E02 | SYSTEM CONFIRMED | app/services/transfers.py:21,34; app/services/replenishment.py:408; app/jobs.py:51 | Explicit dispatch/store count path exists; PO sync can bypass store confirmation for entered transfer batches. No dedicated transfer ingestion found. |
| E03 | SYSTEM CONFIRMED | app/services/stock_events.py:28,64 | Write-off reasons/audit/manual-entry issue exist; delta ≥ half quantity heuristic lacks unique event/transaction matching. |
| E04 | SYSTEM CONFIRMED | app/services/counts.py:72,122,138; app/models.py:493; app/jobs.py | DQ-targeted count and approval/CSV exist; no persisted count match state or verification job found. |
| E05 | SYSTEM CONFIRMED | app/services/purchasing.py:19,37; app/settings_service.py:19 | Existing stale cutoff excludes stale open POs from incoming; physical disposition remains unverified. |
| E06 | SYSTEM CONFIRMED | app/security.py:43; app/models.py:65 | Staff role lacks transfer.receive; reviewed locations are store/warehouse, not bin/rack. Actual staff assignment and bin need remain unverified. |
| E07 | SYSTEM CONFIRMED | SYSTEM_EVIDENCE.json | Read-only operational counts, 5 negative-stock cases, 5 stale POs and domain sync timestamps. No physical root causes attested. |
| E08 | SYSTEM CONFIRMED | app/audit.py:36 / live read-only chain verification | Chain valid=True; rows checked=201; first bad ID=None. Does not prove unrecorded physical events were captured. |
| E09 | SYSTEM CONFIRMED | Existing tests: test_services.py, test_stock_events.py, test_sync.py, test_web.py | Results recorded separately below. Fake StoreHub and disposable DBs; passing existing behavior tests is not full V2.1 acceptance or on-site evidence. |

## Automated verification

Command: `.venv\Scripts\python -m pytest tests/test_services.py tests/test_stock_events.py tests/test_sync.py tests/test_web.py -q`

Result: **78 passed**, 2026-10-09. The suite uses disposable databases and fake StoreHub data. Existing tests verify current behavior, including the stock-delta heuristic; they do not establish compliance with every V2.1 acceptance test or physical warehouse operation. Dependency deprecation warnings were emitted.

Read-only production audit-chain verification: **valid, 201 rows checked**, as of the snapshot. No live event, inventory, PO, user permission or workflow state was changed during this review.

## Snapshot

| Table | Records |
| --- | --- |
| receiving | 0 |
| receiving_line | 0 |
| order_batch | 0 |
| stock_event | 0 |
| cycle_count | 2 |
| cycle_count_line | 31 |

Zero local records mean no evidence in this database; they do not prove no delivery/dispatch/write-off occurred. Sync timestamps and the original baseline hashes are retained in SYSTEM_EVIDENCE.json.

## Open gate disposition

### Gate 1 — Supplier receiving reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency.

**Still required:** Observe an actual delivery: arrival/PO/SKU, count unit, counter role, paper/invoice, physical count versus StoreHub entry timestamps, partial/short/damaged handling, acceptance and stale-PO owner. Resolve checklist 4.1–4.4, 5 and the five PO samples in 17.

**Conclusion:** Keep Receiving Truth and the existing acceptance tests. The physical workflow and partial-receipt semantics remain unverified; do not accept a completed PO or refreshed quantity as evidence of a reconciled physical event.

### Gate 2 — Picking and dispatch reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

Transfer batches counted: 0. Code requires an entered transfer before dispatch and records dispatch actor/time. Batch CSV can supply a list; actual picker use is unknown. The current StoreHub client has no dedicated transfer ingestion endpoint. `sync_batches_with_pos` can change an entered transfer batch linked to a completed PO to received without checking store confirmation (conflict with AT-TRF-02).

**Still required:** Observe one actual request→pick→load→dispatch flow; record request/pick-list source, barcode verification, case packs/substitutions, reference, transfer-entry versus movement times, and dispatch/handoff role. Resolve checklist 7–10.

**Conclusion:** Keep Transfer Truth. Do not treat PO completion as store receipt; record the bypass as an implementation gap against the unchanged acceptance criterion. No dispatch timing policy or scan requirement is promoted from inference.

### Gate 3 — Store receiving reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

The explicit `transfers.receive` path requires a dispatched batch and per-line counts, records receiver/time and opens a variance issue. Transfer batch rows are absent in the snapshot. There is no dedicated StoreHub transfer-state reconciliation in the inspected client/services. Current `transfer.receive` permissions exclude the staff role; store-manager/warehouse/management/admin roles have permission. This does not establish who receives in practice.

**Still required:** Observe or closely validate the same dispatched transfer at the store: counter/confirming role, count versus document, StoreHub stock-increment timing, short/over handling, evidence and who can close it. Resolve checklist 11 and 19 with a store representative.

**Conclusion:** Keep mandatory store confirmation and variance issues. Confirm the real receiving role before proposing any permission change; distinguish a physical confirmation from StoreHub reconciliation.

### Gate 4 — Damage / write-off reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

Stock-event records counted: 0. Write-off capture supports quantity, reason, actor/time, optional photo and audit, with a manual StoreHub-entry issue. Current verification accepts any later same-direction SKU/location delta at least half the recorded quantity within the event lookback, without unique transaction attribution. A sales delta or reused movement can therefore satisfy the heuristic; this is insufficient proof of event reconciliation.

**Still required:** Confirm disposal authority, physical removal versus entry timing, required reasons/photos, recent examples, duplicate/missed entries and supplier returns. Resolve checklist 12–13, including whether write-offs conceal unexplained variance.

**Conclusion:** Keep traceable write-offs and explicit reconciliation evidence. Leave disposal, evidence thresholds and operational duplicate controls open. Flag the heuristic as a gap against Sections 3.4/4; do not weaken those sections.

### Gate 5 — Stock-adjustment reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

Local cycle-count records: 2 headers / 31 lines. Code supports targeted counts from DQ, count actor audit, manager approval and a StoreHub stock-take export. No persisted matched/pending reconciliation state or count verification job was found in the inspected count service/jobs. StoreHub user adjustment permissions are not established by RetailOS roles.

**Still required:** Review actual StoreHub adjustment access with the responsible owner without copying credentials; reconstruct one recent adjustment and the five negative-stock cases. Confirm bulk usage, reason/approval practice, count procedure and whether other process errors are being compensated. Resolve checklist 14–16.

**Conclusion:** Keep Cycle Count + Exception Capture and AT-CNT-02. Count approval or CSV download must not be reported as StoreHub reconciliation. Actual adjustment authority and root causes remain open.

### Gate 6 — Putaway / bin reality

**Status:** OPEN. **Class:** SYSTEM CONFIRMED for code/data only.

The inspected RetailOS location model describes stores/warehouse, not bins/racks; no bin workflow was found in the reviewed models and operational services. Software absence does not establish physical need or current label practice.

**Still required:** Walk a received SKU through putaway and later retrieval. Record zone/rack labels, a recent not-found case, time/value/frequency and staff response; test whether simple labels suffice. Resolve checklist 6 and discuss question 11 in 21.

**Conclusion:** More evidence required. No bin feature or new scope is proposed before operational evidence shows material loss/misplacement.

## Five negative-inventory cases

| Case | Product / barcode | Location | On hand | Indicative deficit value | Cause |
| --- | --- | --- | --- | --- | --- |
| N1 | Baygon Mosq Coil Scented 150g / 4801234134426 | TheDailyCentro Warehouse | -540.0 | 9739.29 | Unverified |
| N2 | Baygon Coil Jumbo Lavander 10s / 4801234134495 | TheDailyCentro Warehouse | -420.0 | 7575.0 | Unverified |
| N3 | Hotdog Roll / 100110 | The Daily Centro - Bantayan | -128.0 | 4480.0 | Unverified |
| N4 | Peach Mango Pie / 100112 | The Daily Centro - Bantayan | -99.0 | 4455.0 | Unverified |
| N5 | Tuna Pie / 100111 | The Daily Centro - Bantayan | -62.0 | 2790.0 | Unverified |

Value is a cost-based prioritization proxy, not a verified inventory loss. Each case requires actual receiving/transfer/return/write-off/adjustment/count history and staff explanation. The JSON contains recent synced PO, sales, stock-change and count references for reconstruction; these do not establish causality. Missing transfer history in RetailOS does not establish missing history in StoreHub.

## Five stale/open PO cases

| Case | StoreHub PO / local ID | Status | Location | Reference date (UTC) | Age days | Physical delivery |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | 36 / 28 | open | TheDailyCentro Warehouse | 2026-08-14 16:00:00.000000 | 55 | Unverified |
| P2 | 101 / 91 | partiallyReceived | TheDailyCentro Warehouse | 2026-09-23 16:00:00.000000 | 15 | Unverified |
| P3 | 112 / 102 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |
| P4 | 114 / 104 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |
| P5 | 115 / 105 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |

Existing system stale cutoff: 7 days. For each case, confirm physically completed/partial/cancelled/abandoned state, document reference, expected-date reliability, close-out owner and whether reminders would help. Leave the observed stale-PO policy decision open. Do not change PO state or count it as genuine incoming based on this review.

## Acceptance evidence versus baseline

| Baseline test | System review disposition |
| --- | --- |
| AT-RCV-01 | Partial: counts/damage/PO/audit/optional photo exist; on-site mobile trace and mandatory variance evidence unverified. |
| AT-RCV-02 | Gap: synced quantities refreshed; no persisted reconciled physical-event state. |
| AT-RCV-03 | Partial: variance issues exist; unresolved event reconciliation semantics missing. |
| AT-TRF-01 | Partial: dispatch actor/time and linked approved lines; actual dispatch quantities/evidence unobserved. |
| AT-TRF-02 | Conflict: explicit receive path enforces count, but PO-sync path can mark entered transfer received without store confirmation. |
| AT-TRF-03 | Partial: count difference opens an issue; real evidence and StoreHub transfer reconciliation unvalidated. |
| AT-CNT-01 | System capability exists: targeted count from DQ. Field usage unobserved. |
| AT-CNT-02 | Gap: approval/CSV exists; persisted pending-to-matched count reconciliation not found. |
| AT-DQ-01 | Not revalidated here; baseline retained. |
| AT-IDEMP-01 | Partial: open-record reuse and issue dedupe exist; complete event/reconciliation coverage not certified. |
| AT-AUD-01 | Live chain valid for 201 rows; full material-action coverage still requires observed event traces. |
| AT-AI-01 | Not completed as a warehouse pilot; isolated deterministic tests do not establish production AI-off operation. |

A development baseline may freeze before all future features are built; these gaps are not automatically new spec scope. They must be acknowledged and dispositioned against the unchanged acceptance criteria. They do not waive the independent physical observation gates.

## Evidence needed to finish

1. Named observer/date and one real supplier delivery reference with count/entry timestamps and evidence.
2. One linked request/pick/dispatch/store receipt reference, roles, times, count and exception/evidence.
3. Staff-reviewed disposal/write-off authority, adjustment access/practice and putaway findings.
4. Physical root-cause conclusions for the five negative cases and delivery disposition/owner for the five stale POs.
5. Warehouse lead/store representative/developer review plus agreement on unchanged boundaries, acceptance tests and metrics.

Once these arrive, update only the six open validation items with verified conclusions/corrections and assess Section 17 before proposing the final FROZEN version. No version or sign-off is fabricated.
