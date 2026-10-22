# QA screenshots with real catalog products

Updated 21 October 2026. Product names, barcodes and categories were read from the catalog using a read-only database connection, then used in an isolated test database. Sales, stock, store names, supplier information and accounts are test fixtures, not live results. Full-page screenshots display this distinction. No production records were changed and no live synchronization was triggered.

Browser verification passed, including product scope, invalid-date handling and mobile picker bounds.

- [Sales by product](sales-by-product.png)
- [Product detail](product-scope.png)
- [Automatic replenishment](automatic-suggestions.png)
- [Sales-only cover explanation](sales-only-cover.png)
- [Invalid date on desktop](invalid-date-desktop.png)
- [Invalid date on mobile](invalid-date-mobile.png)
- [Stock changes text](stock-changes-text.png)

These replace the synthetic-product screenshots in the original QA handoff. They demonstrate UI behavior, not reconciliation of live sales totals. The earlier security and acceptance-testing limitations still apply.
