# Fin MVP domain model

## Workbook evidence

`Bank` separates instrument terms in rows 2–7 from purchases in rows 9–14. It links purchases by exact name and calculates cost, proceeds and annualized yield. The catalog's entered `Target` rate has no observed consumer in the in-scope formulas.

`Overview` combines cash and bank balances, manual exchange rates and Bank outputs. It uses `TODAY()` to identify active holdings and a separate entered date to start the projection. The opening `Free` value uses a foreign-balance toggle. Dated rows after the opening apply purchases, maturity proceeds and first-of-month contributions. The `Fire` rule changes near the end of the projection. [Babki mapping](babki-mapping.md)

## Approved local model

| Record | Fields and relationships |
| --- | --- |
| Opening snapshot | An `id`, an `asOfDate`, manually entered cash and bank amounts by currency, and references to existing bond lots and deposits. Referencing an existing position preserves its original purchase or opening date and its future cashflows. |
| Ukrainian government bond | An `id`, ISIN, currency, denomination, maturity date and optional `target_annual_yield`. Multiple lots can reference the same bond. |
| Bond lot | An `id`, bond reference, purchase date, purchase price, integer quantity, fees and references to scheduled coupon payments where applicable. Currency, denomination and maturity date come from the bond terms. |
| Deposit | An `id`, bank, currency, principal, opening date, maturity date, interest rate, references to the payment schedule and optional `target_annual_yield`. The contract and its principal belong to one deposit. |
| Scheduled payment | An `id`, position reference, payment date, native-currency amount and purpose such as coupon, interest or principal repayment. Dated payments support the explicit bond coupon and deposit payment schedules. |
| FX observation | Base and quote currencies, a rate expressed as quote units per base unit, and an observation date. Rates are entered and edited offline. A rate older than seven days triggers a warning. |
| Projection inputs | Snapshot reference, explicit start date, horizon of up to 50 years, reporting currency defaulting to UAH, monthly contribution, inflation rate and assumed return on free cash. The user can edit the forecast inputs offline. |

Local SQLite stores manually entered records, including market inputs. Explicit backup and restore preserve the inputs, links and missing-value states. Bulk import is deferred.

## Cash and calculation invariants

- Existing holdings were acquired or opened on or before the snapshot date, and their acquisition costs are already reflected in the opening balances. The projection applies later purchase and opening debits to available cash.
- Later scheduled receipts for existing positions still enter the cash projection on their dates. A scheduled principal repayment enters once. Same-date events remain individually identifiable even when the projection shows their net daily effect.
- Scheduled payments are tied to contractual terms, with an explicit coupon or payment schedule as applicable. Keep contractual payment amounts separate from contributions and growth modeled using the assumed return on free cash. The optional `target_annual_yield` field supports comparison only.
- Retain currency on monetary inputs and cashflows. Use Decimal for monetary calculations and keep intermediate precision. Preserve missing values, explicit zero amounts and calculation errors as distinct states. Bond quantities are integers.
- Display UAH, USD and EUR amounts to two decimal places. Use exact assertions where deterministic Decimal arithmetic permits them and document tolerance where Google Sheets comparison requires it.

## Derived views

| Result | Inputs and outputs |
| --- | --- |
| Bank holding summary | Acquisition cost, scheduled coupon or interest payments, and principal or redemption proceeds grouped by holding. Show the optional target alongside any calculated yield for comparison. |
| Bank portfolio summary | Aggregate quantities, costs, fees, and scheduled proceeds. Keep Bank's portfolio-yield comparison distinct from modeled free-cash returns. Yield methods for multiple payments remain under review. |
| Overview summary | Opening balances, total assets, active holding cost, and the workbook-derived summary measures under review, including `Returned` and the daily inflation and yield estimates. |
| Projection point | A calendar date, available cash, modeled return, and inflation measures under the agreed formulas. Show the same daily values in the table and corresponding charts. |

## Proposed calculation conventions

These conventions specify how to apply the approved inputs. [Open questions](open-questions.md) tracks comparisons and mechanics that still need review.

- Treat the snapshot as the closing state on its date and require the projection start to fall on or after it. Carry forward post-snapshot activity before the start, and include start-date activity in the first point if the start is later than the snapshot. Apply each post-snapshot event once. Limit the end date to 50 calendar years from the projection start.
- Store a bond purchase price as currency per unit and compute a lot debit from price times quantity plus entered fees. Use explicit scheduled payment amounts for the whole position in native currency. Price-quote conversion, coupon entitlement and redemption exceptions remain open.
- Use a selected set of FX observations across the projection. Compare each observation date to the current local calendar date when the forecast is viewed, and warn when it is more than seven days old. The snapshot date and future projection dates do not affect the warning. An observation dated after the viewing date is ineligible. The missing-rate behavior remains open.
- Persist monetary inputs losslessly in SQLite, for example as canonical decimal text. Reconstruct Decimal values before calculating and keep formatted text in the presentation layer.
- Round each finalized acquisition debit or scheduled payment in UAH, USD or EUR to two decimals with `ROUND_HALF_UP` before posting it to the cash projection. Keep FX, inflation and modeled return calculations at intermediate precision and apply `ROUND_HALF_UP` to presentation separately. Google Sheets parity at rounding boundaries requires verification.
- Derive purchase debits, payment credits and modeled growth from the local inputs. A daily projection point ties available cash, modeled return and inflation measures to a calendar date. The display formulas, once approved, determine how these values map to `Free`, `Fire`, `Apr` and `Inf`, and charts use the resulting points.
