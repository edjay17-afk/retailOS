# RetailOS UI contract

## Product and visual context

Store staff, warehouse staff and management review actual sales, stock and operations. English and simplified Chinese share the same workflows; local dates use Asia/Manila. This refinement targets keyboard operation, narrow reflow and reduced motion, without claiming formal accessibility certification.

Policy remains in `README.md` (workflow/integration scope), `app/security.py` (permissions/location scope), `app/web/routes_admin.py` (user and supplier lifecycle) and the service modules. This contract records UI consequences without introducing business rules. Runtime visual tokens remain canonical in `app/web/static/workspace.css`, documented in `DESIGN.md`.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Table Selection | Native checkboxes and existing bulk forms | `replenishment.html`, `app.js`, server scope checks | Visible-page selection, permission-dependent bulk actions | Existing tests and browser keyboard check |
| Select/Listbox | Native HTML select | Templates and shared CSS | Browser/OS popup geometry intentionally accepted | Browser keyboard and locale checks |
| Date | Native HTML date fields | Templates and shared CSS | Browser/OS calendar locale and geometry intentionally accepted | Narrow layout and route tests |
| Form | Shared inline validator | `app.js`, HTML constraints and server validation | Login, workspace and externally associated controls | Browser correction and localization tests |
| Scrollbar | Global stylesheet | `workspace.css` | Root, local table overflow and system forced-colours fallback | Computed styles and narrow overflow |
| Toast | Inline feedback | `base.html`, server redirects | Persistent success status, error alert and freshness banner | Route tests and browser feedback |
| CRUD | Existing routes and shared confirmation | `routes_admin.py`, `base.html`, `app.js` | Server-owned redirects and errors; app-owned Cancel/Delete | Admin tests and browser Cancel |

## Navigation, inputs and feedback

Search commits on Enter or the existing Search action. Clear resets editable text and restores input focus; results change after submission. Existing committed filters and pagination stay in URLs. No polling is introduced. Only navigation layout preference persists in local storage.

Inputs retain native keyboard semantics. Textareas grow to 360px and then scroll internally. Shared validation checks existing HTML constraints, renders localized field errors, associates them through `aria-describedby`/`aria-invalid` and focuses the first invalid field. Server validation remains authoritative. Existing AJAX/chat flows retain their recovery handlers. Busy forms prevent duplicate submits without changing labels or dimensions.

User deletion shows object-specific consequence text in an app-owned native dialog, with explicit Delete user action, initial Cancel focus, background isolation, Escape, scroll lock and trigger focus restoration. Disabled self/last-admin actions retain server enforcement. Established admin password visibility is outside this visual refinement.

Replenishment details use a native modal styled as a right drawer; Escape/backdrop/Close restore the opener. Mobile navigation retains its separate backdrop and focus cycle. Inline success/error/freshness messages remain persistent in their existing scope.

## Flow ledger

| Operation | Trigger | Pending | Destination | Recovery | Focus | Source |
|---|---|---|---|---|---|---|
| Search/filter | Enter or Search | Document navigation | Existing query URL | Existing no-results state | Clear restores input | Existing list routes |
| Supplier create/edit | Create supplier or Save | Form submission | Existing list redirect | Server error plus inline constraints | First invalid field | `routes_admin.py` |
| User delete | Delete user then confirm | One confirmed submit | Existing list redirect | Cancel/Escape sends nothing; server checks | Trigger on Cancel | `routes_admin.py` |
| Replenishment detail | Why/decision opener | Existing content | Close returns to list | Existing server/AJAX errors | Opener restored | `replenishment.html` |

## Verification

Run the web/chart/localization/admin suites, JavaScript syntax, DESIGN.md lint and premium static audit. Browser checks cover desktop/mobile dashboard, local table overflow, English/Chinese, search clear, inline correction, drawer and navigation cancellation, and graph keyboard values. Do not submit production business mutations during visual checks. Audit actionless-button findings require checking external listeners; static detection alone cannot establish a runtime failure.
