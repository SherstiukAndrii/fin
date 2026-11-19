# Fin MVP domain model

## Workbook evidence

In `Bank`, rows 2–7 hold instrument terms. Rows 9–14 link purchases to those terms by exact name lookup. The catalog calculates aggregate quantity and maturity proceeds, and the purchase rows calculate total cost, maturity proceeds and annualized yield. The catalog's entered `Target` rate and the calculated purchase yields are distinct fields.

In `Overview`, cash and bank are separate balance categories with UAH, USD and EUR columns. The exchange-rate inputs convert USD and EUR to UAH. The checkbox feeds the calculation of `karta` and thus the initial `Free` value. The `Total` formula includes converted foreign balances independently of the checkbox.

The date used to identify currently held positions comes from `TODAY()`, while the projection starts from the entered date in `Overview!A9`. After the initial row, the `Free` series uses Bank purchases and maturity proceeds at matching dates, plus a monthly contribution on the first of the month. `Fire`, `Apr` and `Inf` have separate rules, including the formula change in `Fire` at row 1003. The [workbook mapping](babki-mapping.md) records the exact source ranges and formula exceptions.

## Proposed local inputs

| Entity | Fields | Relationships and use |
| --- | --- | --- |
| Instrument | `id`, `name`, proposed `kind` (deposit or Ukrainian government bond), `maturityDate`, `payoutPerUnit`, `targetRate`, `currency` | One instrument can have multiple purchases. `payoutPerUnit` represents the catalog `Price` used for maturity proceeds. `targetRate` preserves the entered value, which has no observed consumer in the in-scope formulas. Instrument kind supports the two types in the task brief and requires confirmation because the catalog has no type column. |
| Holding | `id`, `instrumentId`, `purchaseDate`, `unitPurchasePrice`, `quantity`, `feeMode`, `enteredFee` when applicable | A holding represents one purchase. Maturity terms come from its instrument. Proposed fee modes preserve the observed entered amount and formula-based fee. Separate purchases retain separate acquisition dates, prices, quantities and fees. |
| Balance snapshot | `id`, `asOfDate`, cash and bank amounts by currency | Supports one optional amount per category and currency in a snapshot. The optional local record date is a proposed convenience because the source shows a balance grid without a date field of its own. |
| Exchange-rate inputs | `uahPerUsd`, `uahPerEur` | A locally editable pair of quotes for Overview conversion. Both the conversion direction and the currencies are explicit. |
| Projection inputs | `balanceSnapshotId`, FX inputs, `includeForeignInOpeningFree`, `monthlyContribution`, `inflationRate`, `projectionStartDate`, `businessDate` used for evaluation | A projection uses one set of inputs and the Bank catalog and purchases. The opening balance toggle affects only the `karta`/initial `Free` path. Capture the effective business date separately for reproducible comparisons. The projection length and date defaults await approval. |

`Bank!B8` is blank in the workbook and feeds a Bank summary formula. Its role as an editable input requires confirmation. An optional local input can retain this dependency after the rule is approved.

## Derived results

| Result | Fields and relationships |
| --- | --- |
| Holding calculation | One per holding with fee amount, acquisition cost, maturity proceeds and annualized yield derived using the verified Bank rules. |
| Instrument totals | Total purchased quantity and maturity proceeds grouped by instrument. The workbook's exact-name associations and cell ranges define the reference outputs. |
| Bank summary | Proceeds-weighted annualized yield, total proceeds, displayed fee and cost totals, and the monthly estimate. Preserve numeric results and sheet display or parsing behavior separately where the mapping shows formulas depend on formatting. |
| Overview summary | Opening `Free` seed from `karta`, `Total`, `Freezed`, `Returned`, and the daily summary estimates labeled `Inf` and `Dep`. `Freezed` uses purchase date, maturity date and acquisition cost. The exported `Returned` formula needs review before its behavior can be reproduced. |
| Daily projection point | `date`, `free`, `fire`, `apr`, `inf` under the applicable row rules. Purchase debits and instrument maturity credits are derived from the Bank results. Chart series consume the same day values. |

## Proposed invariants

- Use stable local identities and require each holding to reference an existing instrument. During spreadsheet comparison, resolve associations using the workbook's actual lookup ranges and surface missing or ambiguous names. Approval is needed before using a single consistent lookup rule for every row.
- Keep purchase price, maturity payout per unit, entered target rate and calculated yield distinct. Treat an entered fee as a monetary input and a formula-based fee as a derived amount.
- Keep currency on balances and instrument amounts. Use UAH for Bank amounts, as shown in the workbook, and convert foreign cash and bank balances at the points specified by the Overview formulas. Preserve precision through intermediate calculations and apply agreed display formatting and rounding at the mapped steps.
- Preserve blank inputs explicitly so the workbook's deliberate zero and error handling can be reproduced where present. Any app-specific validation for missing rates or invalid quantities and dates needs an agreed response for cases covered by spreadsheet fallbacks.
- Represent dates as calendar dates and keep the effective business date separate from the projection start. Preserve interval boundaries for `Freezed`, the first-of-month contribution trigger, and the workbook's day-count conventions.
- Evaluate balances, holding costs and maturity proceeds in their mapped roles. Add investment cost to the Overview total only through the `Freezed` result, and use purchases and maturity totals on the dates specified by the daily recurrence.
- Recompute outputs from local inputs and approved rules. Keep chart data aligned with the daily projection. The [open questions](open-questions.md) cover policy choices that could change workbook results.
