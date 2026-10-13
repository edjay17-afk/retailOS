# Warehouse On-Site Validation Checklist

**Project:** Retail OS V2.1  
**Purpose:** Validate the real convenience-store central warehouse workflow before V2.1 is frozen  
**Use:** Developer/analyst must complete this on-site with warehouse staff  
**Companion document:** `RETAIL_OS_V2_1_FREEZE_CANDIDATE.md`

**Working validation update:** System review only; field validation incomplete. Original questions retained. Checked boxes indicate only documented decisions (more evidence required / not ready), never an unobserved physical step. Supporting report: [WAREHOUSE_VALIDATION_EVIDENCE.md](WAREHOUSE_VALIDATION_EVIDENCE.md).

---

# 1. Rules for this validation

This is not an interview-only exercise.

At minimum, observe:

1. one real supplier receiving flow;
2. one real warehouse-to-store picking/dispatch flow;
3. one real or closely observed store receiving/confirmation flow if operationally possible.

For every statement, label the evidence as one of:

- **OBSERVED ON-SITE** — directly seen by the reviewer;
- **STAFF CONFIRMED** — stated by responsible warehouse/store staff;
- **SYSTEM CONFIRMED** — supported by StoreHub/RetailOS data or code;
- **INFERRED** — plausible but not yet confirmed.

Do not convert an inference into a product requirement without validation.

Do not assume the current workflow should be digitized exactly as-is.

For every step ask:

> Why does this step exist?  
> What problem is it trying to solve?  
> Should it be kept, improved, automated or removed?

---

# 2. People to involve

Try to include at least:

- warehouse lead/supervisor;
- receiving staff;
- picker/packer;
- dispatch staff;
- driver or delivery coordinator;
- one store manager or store receiving staff;
- developer/reviewer.

Optional if relevant:

- finance/accounting representative;
- operations manager.

---

# 3. Evidence collection

For each major step, collect where allowed:

- photo of paper form / invoice / pick list;
- screenshot of StoreHub screen;
- screenshot of RetailOS/Lark step;
- sample timestamps;
- sample product/SKU;
- sample PO/transfer ID;
- staff role performing the action;
- example of an exception or error.

Do not collect passwords, API secrets or personal sensitive information.

---

# 4. End-to-end supplier receiving observation

## 4.1 Arrival

- [ ] Observe one supplier delivery arriving.
- [ ] Record arrival time.
- [ ] Record supplier name.
- [ ] Record PO/reference used.
- [ ] Who receives the delivery?
- [ ] Is arrival recorded anywhere immediately?
- [ ] Is the delivery ever accepted without a PO?
- [ ] If yes, what happens?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency. Physical arrival/count/entry timing, paper use, partial handling and staff adoption remain unverified.

---

## 4.2 Physical count

- [ ] Are goods actually counted?
- [ ] By piece, case, carton, pallet, or visual estimate?
- [ ] Is barcode scanning used?
- [ ] Are quantities compared to PO?
- [ ] What happens when quantity is short?
- [ ] What happens when quantity is over?
- [ ] What happens when wrong SKU arrives?
- [ ] What happens when product is damaged?
- [ ] Is any photo taken?
- [ ] Is any reason recorded?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency. Physical arrival/count/entry timing, paper use, partial handling and staff adoption remain unverified.

---

## 4.3 StoreHub receiving

- [ ] Who enters receiving into StoreHub?
- [ ] Is it the same person who counts?
- [ ] Is StoreHub entry before, during or after physical count?
- [ ] Typical delay between physical arrival and StoreHub entry?
- [ ] Can it be days later?
- [ ] How are partial receipts handled?
- [ ] How are short receipts handled?
- [ ] Can a PO remain open after physical delivery is already complete?
- [ ] Who closes stale/abandoned POs?
- [ ] Is there a reason field?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency. Physical arrival/count/entry timing, paper use, partial handling and staff adoption remain unverified.

---

## 4.4 RetailOS receiving

- [ ] Is the current RetailOS receiving module known to staff?
- [ ] Have they used it before?
- [ ] If not, why not?
- [ ] Is the flow too slow, unclear, duplicate work, or inaccessible?
- [ ] Can staff realistically use a phone at the receiving dock?
- [ ] Would scanning be easier than typing?
- [ ] What minimum fields would staff tolerate?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Receiving records counted in the local snapshot: 0. Code supports PO-linked counts, damage, optional photos, completion audit and variance issues. `receiving.verify_with_storehub` refreshes PO quantities and flags differences but does not persist a matched/reconciled event state. Variance notes/photos are not mandatory in the current completion path. Open receiving reuse is not proof of end-to-end idempotency. Physical arrival/count/entry timing, paper use, partial handling and staff adoption remain unverified.

---

# 5. Receiving inspection

- [ ] Observe whether quality/damage inspection actually happens.
- [ ] Who performs it?
- [ ] Is there a formal checklist?
- [ ] Is inspection tied to a specific delivery?
- [ ] Is photo evidence used?
- [ ] What happens to damaged cases?
- [ ] Can damaged goods accidentally enter stock?
- [ ] Can rejected goods still appear as received in StoreHub?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Code captures damaged quantity and optional receiving photo; actual inspection, rejection, segregation and checklist practice are unverified.

---

# 6. Putaway / warehouse location

- [ ] Observe how received goods are placed in the warehouse.
- [ ] Are bin/rack/location codes used?
- [ ] If no system location exists, how do staff remember where goods are?
- [ ] How often are goods “not found” despite system stock?
- [ ] What happens when goods cannot be found?
- [ ] Are emergency stock adjustments used?
- [ ] Would simple zone/rack labels solve enough of the problem?

**Decision after observation:**

- [ ] No bin-location feature needed now
- [ ] Simple zone/rack support needed
- [ ] Full bin-level location support needed
- [x] More evidence required

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

The inspected RetailOS location model describes stores/warehouse, not bins/racks; no bin workflow was found in the reviewed models and operational services. Software absence does not establish physical need or current label practice. Decision: more evidence required; do not add bin scope.

---

# 7. Store request / transfer creation

- [ ] How does a store request stock today?
- [ ] RetailOS suggestion?
- [ ] StoreHub transfer?
- [ ] Lark/chat?
- [ ] Phone call?
- [ ] Paper?
- [ ] Who approves the request?
- [ ] Can the same demand be requested twice through different channels?
- [ ] Are informal requests ever fulfilled without a traceable request?
- [ ] Is warehouse stock checked before approving?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Code supports replenishment recommendations, human decisions and batches. Real request channels, informal fulfillment and duplicate-demand handling are unverified.

---

# 8. Picking

- [ ] Observe one real picking process.
- [ ] What document/screen does picker follow?
- [ ] Are SKU and quantity checked?
- [ ] Is barcode scanning used?
- [ ] Are case packs handled correctly?
- [ ] What happens if stock is not found?
- [ ] What happens if actual available quantity is lower than requested?
- [ ] Does picker substitute products without recording?
- [ ] Who verifies the pick?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

CSV export is available; real pick-list use, scanning, substitution and physical pick verification are unverified.

---

# 9. Dispatch

- [ ] Observe one dispatch.
- [ ] Who confirms goods leaving warehouse?
- [ ] When is StoreHub transfer keyed?
- [ ] Before physical movement?
- [ ] During loading?
- [ ] After dispatch?
- [ ] Is dispatch time recorded?
- [ ] Is driver identified?
- [ ] Is package/carton count recorded?
- [ ] Is there proof of handoff?
- [ ] Is RetailOS dispatch confirmation currently used?
- [ ] If not, why not?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Transfer batches counted: 0. Code requires an entered transfer before dispatch and records dispatch actor/time. Batch CSV can supply a list; actual picker use is unknown. The current StoreHub client has no dedicated transfer ingestion endpoint. `sync_batches_with_pos` can change an entered transfer batch linked to a completed PO to received without checking store confirmation (conflict with AT-TRF-02). Actual movement, handoff and entry timing remain unverified.

---

# 10. In transit

- [ ] Is there any tracking between warehouse dispatch and store receipt?
- [ ] Who is responsible during transit?
- [ ] Are shortages/damage/theft ever reported?
- [ ] How are they recorded?
- [ ] Can a delivery disappear operationally until store staff complain?
- [ ] Is a simple dispatched / received status enough for V2.1?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

RetailOS provides dispatched/received workflow statuses. Physical transit responsibility, tracking sufficiency and loss history are unverified.

---

# 11. Store receiving

- [ ] Observe or validate how a store receives a transfer.
- [ ] Does store staff physically count?
- [ ] Do they compare against transfer document?
- [ ] Does StoreHub increase store stock automatically before counting?
- [ ] Who confirms receipt?
- [ ] What happens when actual qty differs?
- [ ] Is shortage/overage recorded?
- [ ] Is photo evidence used?
- [ ] Can a transfer be considered complete without physical confirmation?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

The explicit `transfers.receive` path requires a dispatched batch and per-line counts, records receiver/time and opens a variance issue. Transfer batch rows are absent in the snapshot. There is no dedicated StoreHub transfer-state reconciliation in the inspected client/services. Current `transfer.receive` permissions exclude the staff role; store-manager/warehouse/management/admin roles have permission. This does not establish who receives in practice. No live store count or StoreHub stock-increment timing has been observed.

---

# 12. Returns

- [ ] Who can initiate a return?
- [ ] Store-to-warehouse?
- [ ] Warehouse-to-supplier?
- [ ] Customer returns relevant to stock?
- [ ] Which system records it?
- [ ] Is the correct location/SKU always selected?
- [ ] Is reason required?
- [ ] Is quantity verified physically?
- [ ] Does RetailOS currently see enough information to reconcile it?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Sales ingestion includes return fields; stock-event reasons include supplier return. These do not prove store-to-warehouse/supplier return workflow, physical quantity verification or event reconciliation.

---

# 13. Damage / write-off

- [ ] Who may declare an item damaged?
- [ ] Who may physically dispose of it?
- [ ] Who records it in StoreHub?
- [ ] Is recording immediate or later?
- [ ] Is a reason mandatory?
- [ ] Is photo evidence kept?
- [ ] Are write-offs sometimes forgotten?
- [ ] Are they sometimes entered twice?
- [ ] Are damaged goods ever removed physically before system entry?
- [ ] Is write-off used to “fix” unexplained inventory differences?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Stock-event records counted: 0. Write-off capture supports quantity, reason, actor/time, optional photo and audit, with a manual StoreHub-entry issue. Current verification accepts any later same-direction SKU/location delta at least half the recorded quantity within the event lookback, without unique transaction attribution. A sales delta or reused movement can therefore satisfy the heuristic; this is insufficient proof of event reconciliation. Disposal authority and actual missed/duplicate write-offs require staff and on-site evidence.

---

# 14. Cycle count

- [ ] How often are cycle counts actually done?
- [ ] Who selects what to count?
- [ ] Is counting blind or does staff see expected quantity?
- [ ] How are differences approved?
- [ ] Does staff immediately adjust StoreHub?
- [ ] Is there evidence per SKU?
- [ ] Are repeated differences investigated?
- [ ] Could RetailOS generate targeted counts from DQ findings?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Notes:**

Local cycle-count records: 2 headers / 31 lines. Code supports targeted counts from DQ, count actor audit, manager approval and a StoreHub stock-take export. No persisted matched/pending reconciliation state or count verification job was found in the inspected count service/jobs. StoreHub user adjustment permissions are not established by RetailOS roles. Actual count frequency, blind-count practice, repeated-variance investigation and adjustment timing remain unverified.

---

# 15. Stock adjustments

- [ ] Who has StoreHub adjustment permission?
- [ ] How many people?
- [ ] When is adjustment used?
- [ ] Is reason recorded?
- [ ] Is manager approval required?
- [ ] Are bulk adjustments performed?
- [ ] Is adjustment used to compensate for receiving/transfer/write-off errors?
- [ ] Can reviewer identify a recent real adjustment and explain its cause?

**Evidence status:** SYSTEM CONFIRMED (software/data only); on-site and staff confirmation pending.

**Recent example ID/SKU:** ____________________

**Notes:**

StoreHub adjustment access, approval practice and a recent causal example are unverified. RetailOS count approval permissions do not prove StoreHub permissions.

---

# 16. Negative inventory root-cause sample

Select at least **5 negative-inventory SKU/location cases**, preferably including one severe case.

For each case, try to reconstruct the path.

## Case template

**SKU / Product:** ____________________  
**Location:** ____________________  
**StoreHub on-hand:** ____________________  
**Severity/value:** ____________________

Check:

- [ ] receiving history
- [ ] transfer history
- [ ] sales history
- [ ] return history
- [ ] write-off history
- [ ] stock adjustments
- [ ] cycle counts
- [ ] staff explanation

**Most likely root cause:** ____________________

**Evidence status:** ____________________

**Can V2.1 prevent recurrence?** Yes / No / Partially

**Required change:** ____________________

---

Repeat for at least 5 cases.

---


## Selected system cases for on-site reconstruction

| Case | Product / barcode | Location | On hand | Indicative deficit value | Cause |
| --- | --- | --- | --- | --- | --- |
| N1 | Baygon Mosq Coil Scented 150g / 4801234134426 | TheDailyCentro Warehouse | -540.0 | 9739.29 | Unverified |
| N2 | Baygon Coil Jumbo Lavander 10s / 4801234134495 | TheDailyCentro Warehouse | -420.0 | 7575.0 | Unverified |
| N3 | Hotdog Roll / 100110 | The Daily Centro - Bantayan | -128.0 | 4480.0 | Unverified |
| N4 | Peach Mango Pie / 100112 | The Daily Centro - Bantayan | -99.0 | 4455.0 | Unverified |
| N5 | Tuna Pie / 100111 | The Daily Centro - Bantayan | -62.0 | 2790.0 | Unverified |

Indicative value = absolute negative quantity × synced unit cost; this is a prioritization proxy, not proven loss or an assigned severity. Sampled PO/sales/count/inventory-change references are in SYSTEM_EVIDENCE.json. For every case, physical count, returns/write-offs, adjustment cause and staff explanation remain unverified. No causal conclusion is assigned.

# 17. Stale PO sample

Select at least **5 stale/open POs**.

For each:

- [ ] Was the supplier delivery actually completed?
- [ ] Was it partial?
- [ ] Was it cancelled?
- [ ] Was the PO simply never closed?
- [ ] Is expected date reliable?
- [ ] Who should own close-out?
- [ ] Would a reminder have prevented it?

**Recommended stale-PO rule after observation:**

- [ ] fixed grace period
- [ ] supplier-specific grace period
- [ ] status/manual confirmation required
- [ ] other: ____________________

**Notes:**

SYSTEM CONFIRMED sample below. The current stale cutoff is 7 days using expected date, otherwise creation date; stale open POs are excluded from calculated incoming. This is the existing software rule, not an approved on-site policy. Delivery completion, cancellations, owners and date reliability remain unverified.

| Case | StoreHub PO / local ID | Status | Location | Reference date (UTC) | Age days | Physical delivery |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | 36 / 28 | open | TheDailyCentro Warehouse | 2026-08-14 16:00:00.000000 | 55 | Unverified |
| P2 | 101 / 91 | partiallyReceived | TheDailyCentro Warehouse | 2026-09-23 16:00:00.000000 | 15 | Unverified |
| P3 | 112 / 102 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |
| P4 | 114 / 104 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |
| P5 | 115 / 105 | open | TheDailyCentro Warehouse | 2026-09-24 16:00:00.000000 | 14 | Unverified |


---

# 18. StoreHub usage gaps

For each warehouse process, classify StoreHub use:

| Process | StoreHub used correctly | StoreHub used late | StoreHub not used | StoreHub cannot support | Unknown |
|---|---|---|---|---|---|
| Receiving | ☐ | ☐ | ☐ | ☐ | ☑ |
| Receiving variance | ☐ | ☐ | ☐ | ☐ | ☑ |
| Putaway | ☐ | ☐ | ☐ | ☐ | ☑ |
| Store request | ☐ | ☐ | ☐ | ☐ | ☑ |
| Picking | ☐ | ☐ | ☐ | ☐ | ☑ |
| Dispatch | ☐ | ☐ | ☐ | ☐ | ☑ |
| Store receiving | ☐ | ☐ | ☐ | ☐ | ☑ |
| Returns | ☐ | ☐ | ☐ | ☐ | ☑ |
| Damage/write-off | ☐ | ☐ | ☐ | ☐ | ☑ |
| Cycle count | ☐ | ☐ | ☐ | ☐ | ☑ |
| Stock adjustment | ☐ | ☐ | ☐ | ☐ | ☑ |

For every `StoreHub cannot support`, state whether Retail OS should capture the missing operational context.

---


No process is classified as StoreHub cannot support from code/API absence alone; actual back-office capabilities and use require review.

# 19. Friction test

For each proposed RetailOS capture step, ask the staff member:

- [ ] Can you do this on your phone while working?
- [ ] How many taps is acceptable?
- [ ] Would barcode scan help?
- [ ] Would photo capture help or slow you down?
- [ ] Would you skip this step during busy periods?
- [ ] What would make you actually use it every time?

If a flow is operationally correct but staff will not use it, it is not production-ready.

---

# 20. Final warehouse event map

After observation, update each event with evidence status.

| Event | Real current process | Tool | Timing | Evidence status | Failure mode | V2.1 decision |
|---|---|---|---|---|---|---|
| Supplier receiving | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Receiving inspection | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Putaway | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Store request | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Picking | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Dispatch | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| In transit | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Store receiving | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Returns | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Damage/write-off | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Cycle count | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |
| Stock adjustment | Unverified | Actual use unverified | Unverified | No on-site/staff evidence | Not established | Keep open; see evidence report |

---

# 21. Required conclusions before V2.1 freeze

Reviewer must answer clearly:

1. Where is supplier receiving truth created today?
2. Where is warehouse dispatch truth created today?
3. Where is store receiving truth created today?
4. Which inventory-changing events currently have no actor/reason/evidence?
5. Which bad-data problems are caused by process non-compliance?
6. Which are caused by StoreHub limitations?
7. Which are caused by RetailOS adoption/friction?
8. Which are caused by incorrect or missing master data?
9. Which current steps should be removed rather than digitized?
10. Which 3 changes will prevent the most new bad inventory data?
11. Does Retail OS need bin-location support now, later, or not yet?
12. Can staff realistically complete receiving and transfer confirmations on mobile?
13. What must be changed in `RETAIL_OS_V2_1_FREEZE_CANDIDATE.md` before freeze?

---


### Current reviewer conclusions

Questions 1–9 and 11–12 remain open pending direct observations and responsible staff evidence. Zero records do not distinguish adoption friction, process non-compliance or missing event capture. For question 10, the baseline priorities remain receiving truth, dispatch/store receiving confirmation, and targeted counts/exception capture; their actual impact ranking is unvalidated. For question 13, only Section 16 validation evidence/status has been proposed for update; no architecture, metric, ownership or scope changes are justified by the evidence available.

# 22. Final validation sign-off

**Observed by:** ____________________  
**Date:** ____________________  
**Warehouse lead reviewed:** Yes / No  
**Store representative reviewed:** Yes / No  
**Developer reviewed:** Yes / No

### Overall evidence quality

- [ ] High — core receiving and transfer flows observed directly
- [ ] Medium — some steps observed, some only staff-confirmed
- [ ] Low — mainly inferred / interview-based

### Recommendation

- [ ] Ready to freeze V2.1
- [ ] Ready after minor spec corrections
- [x] Not ready — major workflow assumptions still unverified

### Required spec changes before freeze

1. __________________________________________
2. __________________________________________
3. __________________________________________
4. __________________________________________
5. __________________________________________


**Remote system review only:** 2026-10-09. No on-site observer, warehouse/store approval or actual flow observation is attested. Overall on-site evidence quality is unassessed; no quality box is selected. Final sign-off remains blank. The software findings are implementation gaps against the existing requirements, not approved spec changes.
