# Fin MVP open questions

The [MVP specification](mvp.md) and [domain model](domain-model.md) record the approved scope and inputs. The [Babki mapping](babki-mapping.md) records the workbook formulas and their exceptions. The live sheet was blocked by the environment proxy, so the XLSX and CSV remain the comparison sources.

## Workbook behavior awaiting approval

| Area | Observed in the export | Decision needed |
| --- | --- | --- |
| Fire | `Overview!C9` starts at zero. `C10:C1002` accumulates a daily return on prior-day Free using the Bank portfolio yield. `C1003:C1007` instead compounds current Free from the start date and includes principal. | Should Fin use the early recurrence consistently with the editable free-cash return assumption, or should it reproduce the final switch? Confirm the intended meaning of Fire and expected results on both sides of the switch. For a workbook comparison, set the editable assumption to the workbook yield. |
| Returned | `Overview!F2` parses the formatted gross expected payout in `Bank!F8` through a Google Sheets expression with an export fallback. The live result and the fallback's intent remain unverified. | Should Returned mean gross scheduled payouts, proceeds actually received, or another total? If it means scheduled payouts, confirm which holdings and dates contribute. If it means receipts, confirm the evidence Fin should use to recognize payment. Provide an expected result for a case with both a future and a past payout. |

## Calculation policies to confirm with fixtures

| Area | Decision or evidence needed |
| --- | --- |
| Bank fee and yield displays | Confirm whether Bank should retain the workbook's simple 365-day annualization and payout-weighted portfolio yield when a holding has coupons or multiple interest payments. If retained, specify which amount and dates feed each yield. Confirm whether lot fees are entered explicitly for every purchase or can be seeded by a fee rule. |
| Scheduled versus actual payments | Confirm whether projections assume each contractual payment occurs on its scheduled date. Include an example with an unpaid or delayed payment if Fin must distinguish it from a received payment. Clarify whether coupon and interest amounts should enter the forecast gross or net when withholding is relevant. |
| Bank monthly estimate | Confirm whether to surface the workbook's Bank monthly estimate separately from Overview's editable monthly contribution. If retained, confirm the role of the blank `Bank!B8` adjustment and whether the estimate uses a rounded display total or the underlying acquisition cost. |
| Overview valuation and FX coverage | Confirm whether `Total` should value active investments at acquisition cost on the snapshot date, and whether the workbook’s foreign-balance toggle still applies only to opening `Free`. Define the result if a balance or contractual payment lacks a conversion rate. |
| Inflation labels | The workbook has separate summary `Inf`, daily `Apr`, and daily `Inf` values. Confirm the display names and units. Use a fixture with purchase or maturity activity and nonzero inflation to compare the fixed starting Total in the workbook to the intended app result. |

## Verification details

- Keep the workbook's special ranges, row 9 fee omission, zero-on-error behavior, and final undated chart point documented in the mapping. Compare them when diagnosing differences between workbook results and the app's typed, dated records.
- Use explicit dates in calculation fixtures. Include events on the snapshot date, in the gap before projection start, and on the first projection date. Also include a first-of-month contribution and a projection spanning a leap day.
- Verify a bond with fees and a coupon and a deposit with a specified payment schedule. Include missing, zero, and invalid inputs as distinct cases. Test a missing FX quote, a six-day-old quote, a seven-day-old quote, and an eight-day-old quote against the current local calendar date.
- Record exact decimal expectations where possible. Define comparison tolerances separately for Sheets outputs that depend on formatted text or spreadsheet arithmetic, and record any approved changes in the mapping.
