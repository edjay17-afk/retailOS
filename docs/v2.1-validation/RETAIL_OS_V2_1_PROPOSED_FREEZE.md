# Retail OS V2.1 — Freeze Candidate

**Status:** FREEZE CANDIDATE  
**Version:** 2.1-FC1  
**Purpose:** Development baseline candidate pending warehouse on-site validation  
**Primary users:** Convenience-store warehouse team, store managers, operations management, developer/Claude Code  
**Source basis:** Retail OS V2 specification, Programmer Technical Review v2, AI-Native Product Benchmark EN-ZH v4, current RetailOS production data snapshot as of 2026-10-08

---

## 0. Freeze rule

This document is the candidate development baseline for Retail OS V2.1.

Before `Status` may change from `FREEZE CANDIDATE` to `FROZEN`, all items marked **OPEN VALIDATION** must be validated on-site in the convenience-store central warehouse and any resulting corrections must be incorporated.

After V2.1 is frozen:

- Architecture boundaries may not be changed casually.
- Inventory ownership rules may not be changed without a written spec change.
- AI autonomy rules may not be loosened without approval and evidence.
- Business logic changes must have acceptance tests.
- Claude Code must read this document before implementing any V2.1 task.

---

# 1. Product objective

Retail OS is an **AI-native retail operations layer on top of StoreHub**.

Its purpose is to:

1. create reliable operational ground truth where StoreHub alone is insufficient;
2. make inventory-changing physical events traceable;
3. detect and surface exceptions instead of hiding them;
4. generate deterministic, explainable operational recommendations;
5. let AI investigate, explain and propose actions using governed context;
6. keep humans in control of high-risk actions;
7. gradually automate only decision classes that have earned autonomy through evidence;
8. allow operations to continue normally even when the AI provider is unavailable.

The goal is **management by exception**, not more dashboards.

---

# 2. Non-negotiable architecture

## 2.1 System roles

### StoreHub

**Role:** canonical transactional source for current POS/store inventory, sales, products and StoreHub PO status.

StoreHub remains the canonical POS inventory source during V2.1.

### Retail OS backend

**Role:** operational data layer, business rules, workflow state, audit, data-quality engine, recommendation engine, reconciliation and AI context/tool layer.

All core business logic must run here.

### Lark

**Role:** human interaction, alerts, lightweight approvals, reminders, deep links and read-oriented operational views.

Lark is not the business-logic engine and must not become a second source of truth.

### AI layer

**Role:** explain, investigate, summarize, answer questions, identify likely causes, compare scenarios and propose governed actions.

The AI must not directly become the source of numeric truth or the canonical inventory ledger.

---

## 2.2 Core design boundary

**Code calculates; AI explains.**

Deterministic software must calculate:

- inventory positions;
- sales totals;
- replenishment quantities;
- monetary amounts;
- policy thresholds;
- permissions;
- reconciliation status;
- acceptance metrics;
- audit integrity.

AI may:

- explain why a recommendation was produced;
- summarize exceptions;
- investigate likely operational causes;
- propose next actions;
- turn approved business rules into structured candidate rules;
- use approved tools only through the policy gate.

AI must not:

- directly calculate the canonical order quantity using free-form reasoning;
- directly update StoreHub inventory;
- directly run SQL;
- silently modify canonical data;
- auto-execute high-risk actions;
- treat an unverified human correction as policy.

---

# 3. Inventory truth model

Retail OS must not create a second hidden inventory ledger.

The following concepts must remain separate.

## 3.1 `storehub_on_hand`

The latest synced StoreHub quantity on hand for a product/location.

This is the canonical POS inventory number during V2.1.

## 3.2 `physical_observation`

A human-observed physical fact, such as:

- counted receiving quantity;
- cycle-count quantity;
- store receiving count;
- damaged quantity;
- return quantity;
- write-off quantity.

A physical observation does **not** automatically overwrite StoreHub inventory.

## 3.3 `pending_inventory_event`

A traceable inventory-changing event captured in Retail OS that has not yet been confirmed in StoreHub.

Example:

```text
StoreHub on-hand: 100
Physical event: write-off -5
Retail OS status: pending reconciliation
Expected physical effect: 95
```

## 3.4 `reconciled_inventory_event`

A pending inventory event becomes reconciled only after the corresponding StoreHub state or confirmed transaction supports the event.

Example:

```text
StoreHub on-hand after sync: 95
Event: write-off -5
Status: reconciled
```

## 3.5 `expected_physical_inventory`

Retail OS may calculate an expected physical inventory for investigation and reconciliation, but it must be explicitly labeled as **expected**, not canonical StoreHub inventory.

---

# 4. Inventory event model

Every real-world event that changes inventory must leave a trace.

## 4.1 Event types

At minimum:

- supplier_receiving
- receiving_variance
- transfer_dispatch
- store_receiving
- transfer_variance
- return
- damage
- write_off
- cycle_count
- stock_adjustment
- manual_correction

## 4.2 Required event fields

Each inventory-changing event must record, where applicable:

- `event_id`
- `event_type`
- `product_id`
- `location_id`
- `quantity`
- `direction` (+ / -)
- `actor_user_id`
- `actor_role`
- `occurred_at`
- `recorded_at`
- `reason_code`
- `reason_text`
- `evidence_photo_or_file`
- `source_system`
- `storehub_reference`
- `related_po_id`
- `related_transfer_id`
- `reconciliation_status`
- `reconciled_at`
- `reconciliation_note`
- `audit_event_id`

## 4.3 Reconciliation status

Allowed statuses:

- `pending`
- `matched`
- `mismatch`
- `needs_review`
- `rejected`
- `cancelled`

---

# 5. Ground-truth principles

## Principle 1 — Ground truth is captured, not assumed

Fix records as close as possible to the moment the physical event occurs.

## Principle 2 — Code calculates; AI explains

Money- and quantity-changing decisions remain deterministic.

## Principle 3 — Refuse loudly, with the reason

If the system cannot trust a recommendation, it must refuse and tell the user exactly what needs fixing.

## Principle 4 — Every recommendation is replayable

Formula version + input snapshot + outcome must allow reproduction.

## Principle 5 — Autonomy is earned

Autonomy depends on decision class, risk, confidence, reversibility, historical acceptance, outcome quality, amount, minimum observation period and minimum sample size.

## Principle 6 — Corrections are candidate context

A manager override is not a rule until the outcome supports it.

## Principle 7 — Degrade gracefully

The operational loop must remain complete with AI turned off.

## Principle 8 — Meet people where they already work

Use mobile capture, Lark nudges and low-friction approvals.

## Principle 9 — Every inventory-changing event leaves a trace

No unexplained physical inventory movement should be accepted as normal.

---

# 6. V2.1 MUST-build scope

The MUST-build scope is intentionally focused on stopping new bad data before cleaning all historical bad data.

## MUST 1 — Receiving Truth

### Problem

Supplier receiving is recorded in StoreHub, but counted physical receiving is not consistently captured in Retail OS. Stale POs and unverified quantities make incoming inventory unreliable.

### Goal

Create a standard receiving loop:

```text
PO due / delivery arrives
→ physical count on phone
→ compare against PO
→ record variance + evidence
→ complete/adjust StoreHub receiving process
→ next sync verifies StoreHub state
→ Retail OS reconciles event
→ mismatch creates issue
```

### Requirements

- mobile-first receiving form;
- barcode scan where available;
- ordered quantity visible;
- counted quantity required;
- damaged/short/over quantity supported;
- reason required for variance;
- optional/required photo depending on variance class;
- actor and timestamp captured;
- event saved before reconciliation;
- StoreHub confirmation checked on next sync;
- unresolved mismatch becomes an issue;
- receiving event is idempotent.

### Primary KPIs

- % completed POs with matched counted-receiving record;
- receiving confirmation rate;
- receiving variance rate;
- average time from physical arrival to recorded receiving;
- % receiving events reconciled with StoreHub.

### Initial target

- receiving confirmation rate >90% for pilot flows.

---

## MUST 2 — Transfer / Store Receiving Truth

### Problem

Dispatch and store receipt are not consistently confirmed, so inventory can disappear in transit or be silently absorbed by system adjustments.

### Goal

Create a traceable transfer loop:

```text
Store request / transfer plan
→ pick
→ dispatch confirmation
→ in transit
→ store physical count
→ store confirmation
→ variance
→ issue if mismatch
→ reconciliation
```

### Requirements

- dispatch event with actor/time;
- optional barcode/scan verification for high-risk or pilot categories;
- store receiving count;
- cannot mark transfer closed in Retail OS without store confirmation;
- variance must open an issue;
- evidence stored for material mismatches;
- StoreHub transfer state must be reconciled against Retail OS events;
- informal chat/phone request may be converted into a traceable request before fulfillment.

### Primary KPIs

- transfer confirmation rate;
- % dispatched transfers with store receiving confirmation;
- in-transit variance rate;
- unexplained transfer delta rate;
- average dispatch-to-confirmation time.

---

## MUST 3 — Cycle Count + Inventory Exception Capture

### Problem

Historical inventory is already materially wrong, but bulk adjustment without evidence can make metrics look clean without fixing the underlying process.

### Goal

Use targeted cycle counts to repair the highest-risk inventory records while preserving evidence.

### Requirements

- DQ findings can directly open a cycle-count task;
- priority by severity/value/frequency;
- mobile count form;
- counted quantity, actor, time and optional photo;
- reason required if adjustment is proposed;
- adjustment remains pending until StoreHub is updated/confirmed;
- reconciliation and audit are mandatory;
- repeated variance on the same SKU/location raises a root-cause issue.

### Primary KPIs

- cycle-count accuracy;
- repeat variance rate;
- unexplained inventory-delta rate;
- % inventory-changing events with trace;
- negative inventory rows as an outcome KPI only.

**Do not use “negative rows reduced” as the only or primary success measure.**

---

## MUST 4 — Loud Refusal + Data Repair Queue

### Problem

Untrusted replenishment recommendations are currently blocked by missing or bad data.

### Goal

Turn `needs_review` into an actionable repair queue.

### Requirements

Every blocked recommendation must show:

- the exact blocking reason;
- affected SKU/location;
- why the recommendation cannot be trusted;
- direct link to the repair action;
- severity/value at risk;
- whether the block is caused by negative stock, missing supplier, stale PO, conflicting PO, stale data or other supported reason.

### Primary KPIs

- value unblocked per week;
- open blocked-value trend;
- mean time to repair blocking data;
- repeat blocker rate.

---

# 7. V2.1 NEXT scope

## NEXT 1 — Replay Gate + Outcome-linked Overrides

- Every recommendation stores formula version and input snapshot.
- Replay must reproduce historical recommendations.
- Manager override + reason + actual outcome are linked.
- A correction pattern may influence future recommendations only after measured improvement.
- Reports must refuse to conclude when sample size is too small.

### Minimum evidence rule

No policy promotion based on anecdotal data.

A future policy change requires:

- minimum observation period;
- minimum sample size;
- no critical safety failure;
- measurable non-inferior or improved outcome.

---

## NEXT 2 — Autonomy Scorecard per Decision Class

Autonomy must progress through:

```text
Shadow
→ Suggest
→ Human approve
→ Low-risk assisted execution
→ Auto-execute
```

Auto-execution requires **all** of the following:

- approved decision class;
- low enough risk;
- data freshness acceptable;
- data confidence acceptable;
- reversible or recoverable action;
- amount below policy threshold;
- minimum observation period met;
- minimum sample size met;
- historical acceptance threshold met;
- outcome quality threshold met;
- zero unresolved critical failures in evaluation window.

No global auto-execution switch should bypass this policy.

---

## NEXT 3 — Lark Action Loop

- alerts with deep links;
- approve/reject for approved low-risk classes;
- permission and amount checks on server side;
- actor, policy-gate result and final action logged;
- high-risk actions stay approval-only.

---

# 8. V2.1 LATER scope

## LATER 1 — Expiry Capture + FEFO

Do not require expiry capture for every product or every physical case.

Start only with expiry-managed/perishable categories.

Preferred capture unit:

```text
SKU × receiving batch × expiry date × quantity
```

Example:

```text
SKU: Coke 1.5L
Received qty: 480
Expiry: 2027-03-01
```

not 20 separate case-level entries unless operationally necessary.

Later functions:

- expiry warnings;
- FEFO pick order;
- expiry value at risk;
- write-off/markdown suggestions.

---

## LATER 2 — AI Reliability Transparency

Low-cost usability improvement may be implemented earlier if convenient:

- cache last-good AI brief;
- show timestamp/age;
- show current provider unavailable;
- provider failure metrics;
- AI failure must never block operations.

---

# 9. Metric definitions

## 9.1 Sales–Inventory Inconsistency Rate

Do **not** call this “phantom stockout rate” unless inventory is time-aligned to the sale event.

Current acceptable definition:

> % of sold product-days whose available same-day inventory snapshot shows on-hand <= 0.

This is an inconsistency indicator, not proof of exact-time stockout.

Future exact stockout metrics require time-aligned inventory history or reconstructable inventory state.

---

## 9.2 Inventory Event Traceability %

```text
traceable inventory-changing events
÷
all detected inventory-changing events
```

A traceable event has at minimum actor, quantity, timestamp and reason/source.

---

## 9.3 Unexplained Inventory Delta Rate

```text
inventory deltas with no matching known event
÷
all inventory deltas
```

This is a primary operational-health KPI.

---

## 9.4 Receiving Confirmation Rate

```text
completed deliveries with matched physical receiving record
÷
completed deliveries
```

---

## 9.5 Transfer Confirmation Rate

```text
transfers with dispatch + store receiving confirmation
÷
all dispatched transfers
```

---

# 10. AI context and evidence rules

AI context must be built by code and versioned.

AI outputs must store:

- prompt version;
- context-builder version;
- provider/model;
- facts snapshot or hash;
- cited evidence references;
- user identity and scope;
- tool proposals;
- policy-gate result;
- final approved/rejected action.

Manager corrections may enter future context only as **candidate facts** unless the outcome validates them.

---

# 11. Permissions and approval

At minimum:

- Staff
- Store Manager
- Warehouse
- Management
- Admin

Server-side authorization must check:

- user role;
- location scope;
- action risk class;
- amount threshold;
- data freshness;
- confidence;
- autonomy status.

Hidden UI buttons are not security.

---

# 12. Audit requirements

Every material operational action must be auditable.

Audit fields include:

- actor;
- actor type (human / AI / system);
- before/after state;
- reason;
- related entity;
- timestamp;
- policy decision;
- approval decision;
- source/evidence;
- immutable hash-chain linkage.

Audit retention target: **3 years** unless superseded by legal/business policy.

---

# 13. Data-quality priorities

Retail OS should continuously detect at minimum:

- negative inventory;
- missing supplier;
- missing barcode/SKU;
- stale PO;
- conflicting/open PO;
- cost mismatch;
- duplicate/inconsistent category;
- stale sync/data freshness;
- receiving mismatch;
- transfer mismatch;
- repeated cycle-count variance;
- unexplained inventory delta.

Data-quality issues should create repairable work, not passive warnings.

---

# 14. Master-data cleanup rules

Data cleanup runs in three layers:

```text
RAW
→ PROPOSED/CLEANED
→ APPROVED/TRUSTED
```

Rules:

- Raw exports never change.
- Claude Code may profile and propose transformations.
- Missing operational values must not be guessed.
- Supplier mapping may be suggested from PO history, then confirmed by warehouse/business owner.
- Lead time, case pack, MOQ/order multiple require human confirmation if not supported by data.
- No AI-cleaned output may overwrite StoreHub automatically.

Initial prioritization:

- top-selling 200–300 SKUs first;
- then high-value/high-variance items;
- then long tail.

---

# 15. Acceptance tests — MUST scope

## AT-RCV-01 Receiving trace

Given a test delivery, when warehouse staff count it on mobile, then Retail OS records actor, quantity, time, reason/evidence and related PO.

## AT-RCV-02 Receiving reconciliation

After StoreHub receiving is completed and sync runs, Retail OS marks the physical event reconciled if quantities agree.

## AT-RCV-03 Receiving mismatch

If physical count and StoreHub differ, an issue is created automatically and the event remains unresolved.

## AT-TRF-01 Dispatch trace

A transfer dispatch must record actor, time and quantity.

## AT-TRF-02 Store confirmation

A dispatched transfer cannot be marked complete in Retail OS without store confirmation.

## AT-TRF-03 Transfer mismatch

A dispatch/store-count difference opens an issue.

## AT-CNT-01 Count from DQ issue

A negative-stock DQ finding can create a cycle-count task directly.

## AT-CNT-02 Count reconciliation

A count adjustment remains pending until StoreHub reflects the approved correction.

## AT-DQ-01 Loud refusal

A blocked replenishment recommendation displays exact cause and direct repair link.

## AT-IDEMP-01 Idempotency

Re-running any sync, event-ingest or reconciliation job must not create duplicate business events.

## AT-AUD-01 Audit integrity

All material actions create audit entries; audit-chain verification remains valid after test completion.

## AT-AI-01 AI-off operation

With AI providers disabled, receiving, transfer, inspection, DQ and deterministic replenishment workflows remain usable.

---

# 16. On-site validation gates before V2.1 freeze

**Validation update: 2026-10-09 10:08 +0800**

This is a proposed update to open validation items only. All six gates remain **OPEN VALIDATION**. No completed on-site notes or staff sign-off were supplied. Software/data checks below are **SYSTEM CONFIRMED**, not **OBSERVED ON-SITE** or **STAFF CONFIRMED**. The companion working checklist and evidence report identify what must be observed before the final freeze can be issued.

Supporting files: [validation evidence](WAREHOUSE_VALIDATION_EVIDENCE.md), [working checklist](WAREHOUSE_ONSITE_VALIDATION_WORKING.md), [snapshot](SYSTEM_EVIDENCE.json). Zero local records indicate missing RetailOS evidence, not proof of physical non-compliance. Passing current automated tests does not close a physical gate or certify all V2.1 requirements.

## OPEN VALIDATION 1 — Supplier receiving reality

Confirm:

- who physically counts;
- when StoreHub is updated;
- whether paper is used;
- how partial receiving is handled;
- whether damaged goods are recorded before acceptance;
- how stale POs are currently closed.

**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency.

**Required on-site evidence:** Observe an actual delivery: arrival/PO/SKU, count unit, counter role, paper/invoice, physical count versus StoreHub entry timestamps, partial/short/damaged handling, acceptance and stale-PO owner. Resolve checklist 4.1–4.4, 5 and the five PO samples in 17.

**Proposed conclusion:** Keep Receiving Truth and the existing acceptance tests. The physical workflow and partial-receipt semantics remain unverified; do not accept a completed PO or refreshed quantity as evidence of a reconciled physical event.

---

## OPEN VALIDATION 2 — Picking and dispatch reality

Confirm:

- source of pick list;
- whether scan verification exists;
- when transfer is entered;
- whether transfer entry occurs before or after physical movement;
- who confirms dispatch.

**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** Transfer batches counted: 0. Code requires an entered transfer before dispatch and records dispatch actor/time. Batch CSV can supply a list; actual picker use is unknown. The current StoreHub client has no dedicated transfer ingestion endpoint. `sync_batches_with_pos` can change an entered transfer batch linked to a completed PO to received without checking store confirmation (conflict with AT-TRF-02).

**Required on-site evidence:** Observe one actual request→pick→load→dispatch flow; record request/pick-list source, barcode verification, case packs/substitutions, reference, transfer-entry versus movement times, and dispatch/handoff role. Resolve checklist 7–10.

**Proposed conclusion:** Keep Transfer Truth. Do not treat PO completion as store receipt; record the bypass as an implementation gap against the unchanged acceptance criterion. No dispatch timing policy or scan requirement is promoted from inference.

---

## OPEN VALIDATION 3 — Store receiving reality

Confirm:

- whether store staff physically count;
- whether StoreHub auto-increments without count;
- how shortages/overages are handled;
- whether any evidence is retained.

**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** The explicit `transfers.receive` path requires a dispatched batch and per-line counts, records receiver/time and opens a variance issue. Transfer batch rows are absent in the snapshot. There is no dedicated StoreHub transfer-state reconciliation in the inspected client/services. Current `transfer.receive` permissions exclude the staff role; store-manager/warehouse/management/admin roles have permission. This does not establish who receives in practice.

**Required on-site evidence:** Observe or closely validate the same dispatched transfer at the store: counter/confirming role, count versus document, StoreHub stock-increment timing, short/over handling, evidence and who can close it. Resolve checklist 11 and 19 with a store representative.

**Proposed conclusion:** Keep mandatory store confirmation and variance issues. Confirm the real receiving role before proposing any permission change; distinguish a physical confirmation from StoreHub reconciliation.

---

## OPEN VALIDATION 4 — Damage / write-off reality

Confirm:

- who may dispose of damaged stock;
- when write-off is recorded;
- whether reason codes exist;
- whether photos/evidence are kept;
- whether duplicate/missed write-offs occur.

**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** Stock-event records counted: 0. Write-off capture supports quantity, reason, actor/time, optional photo and audit, with a manual StoreHub-entry issue. Current verification accepts any later same-direction SKU/location delta at least half the recorded quantity within the event lookback, without unique transaction attribution. A sales delta or reused movement can therefore satisfy the heuristic; this is insufficient proof of event reconciliation.

**Required on-site evidence:** Confirm disposal authority, physical removal versus entry timing, required reasons/photos, recent examples, duplicate/missed entries and supplier returns. Resolve checklist 12–13, including whether write-offs conceal unexplained variance.

**Proposed conclusion:** Keep traceable write-offs and explicit reconciliation evidence. Leave disposal, evidence thresholds and operational duplicate controls open. Flag the heuristic as a gap against Sections 3.4/4; do not weaken those sections.

---

## OPEN VALIDATION 5 — Stock-adjustment reality

Confirm:

- who has StoreHub adjustment permission;
- when adjustments are used;
- whether reasons are recorded anywhere;
- whether bulk adjustments happen;
- whether an adjustment is sometimes used to compensate for another process failure.

**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** Local cycle-count records: 2 headers / 31 lines. Code supports targeted counts from DQ, count actor audit, manager approval and a StoreHub stock-take export. No persisted matched/pending reconciliation state or count verification job was found in the inspected count service/jobs. StoreHub user adjustment permissions are not established by RetailOS roles.

**Required on-site evidence:** Review actual StoreHub adjustment access with the responsible owner without copying credentials; reconstruct one recent adjustment and the five negative-stock cases. Confirm bulk usage, reason/approval practice, count procedure and whether other process errors are being compensated. Resolve checklist 14–16.

**Proposed conclusion:** Keep Cycle Count + Exception Capture and AT-CNT-02. Count approval or CSV download must not be reported as StoreHub reconciliation. Actual adjustment authority and root causes remain open.

---

## OPEN VALIDATION 6 — Putaway / bin reality

Confirm whether missing bin locations materially cause lost/misplaced stock before deciding to build bin-location support.


**Gate status:** OPEN — on-site evidence and responsible reviewer conclusion pending.

**Evidence status:** SYSTEM CONFIRMED for the software/data observations below only.

**System evidence:** The inspected RetailOS location model describes stores/warehouse, not bins/racks; no bin workflow was found in the reviewed models and operational services. Software absence does not establish physical need or current label practice.

**Required on-site evidence:** Walk a received SKU through putaway and later retrieval. Record zone/rack labels, a recent not-found case, time/value/frequency and staff response; test whether simple labels suffice. Resolve checklist 6 and discuss question 11 in 21.

**Proposed conclusion:** More evidence required. No bin feature or new scope is proposed before operational evidence shows material loss/misplacement.

---

# 17. Freeze criteria

V2.1 may be marked `FROZEN` when:

1. at least one real supplier receiving flow is observed;
2. at least one real warehouse-to-store dispatch/receiving flow is observed;
3. all six OPEN VALIDATION items have evidence status and conclusions;
4. architecture boundaries in Sections 2–4 are accepted;
5. MUST scope acceptance tests are agreed;
6. metric names/definitions are agreed;
7. no unresolved contradiction remains between actual warehouse operation and the spec.

---

# 18. Development sequence after freeze

Recommended order:

```text
1. Receiving Truth
2. Transfer / Store Receiving Truth
3. Cycle Count + Exception Capture
4. Loud Refusal + Data Repair Queue
5. Replay + Outcome Tracking
6. Lark Action Loop
7. Autonomy Scorecards
8. Expiry / FEFO
9. Additional AI enhancements
```

AI features must not jump ahead of missing operational ground truth.

---

# 19. Claude Code execution instruction

Before implementing any V2.1 task, Claude Code should:

1. read this entire specification;
2. identify the exact section being implemented;
3. identify affected data ownership boundaries;
4. list acceptance tests before coding;
5. preserve StoreHub as canonical POS inventory source;
6. preserve idempotency and audit behavior;
7. avoid introducing business logic into Lark;
8. update tests together with implementation;
9. report any conflict between code reality and this spec instead of silently improvising;
10. request spec clarification before changing architecture, inventory ownership or AI autonomy behavior.

---

# 20. Change-control note

After freeze, any proposal that changes one of the following requires a written spec change:

- source of truth;
- inventory ownership;
- reconciliation logic;
- replenishment formula;
- permission model;
- approval rules;
- AI autonomy thresholds;
- audit requirements;
- warehouse event semantics.

Feature-level UI improvements that do not alter these may proceed through normal development review.
