# Fin MVP open questions

The [Babki mapping](babki-mapping.md) records formulas from the supplied XLSX and uses the Overview CSV as a check on displayed values. The live sheet was blocked by the environment proxy. The questions below mark where the export leaves intent uncertain or where matching its behavior requires a product decision.

## Formula decisions that affect parity

| Area | Observed in the export | Decision for approval |
| --- | --- | --- |
| Fire recurrence | `Overview!C9` starts at zero. `C10:C1002` adds growth on the prior day's Free to the previous Fire. `C1003:C1007` instead compounds the current day's Free from the start date. | Should Fin reproduce the switch or apply one rule to the whole projection? If one rule is intended, which rule and which values should appear around the switch? |
| Final projection row | `Overview!B1008` continues the Free formula with a blank date and is included in the first chart's Free range. Dated rows end at 1007. | Should the app include the final undated result, or stop at the last dated point? Confirm the intended projection length or end date. |
| As-of and projection dates | `Overview!F1` evaluates holdings using `TODAY()`. `A9` is a separate stored date for the first projection row. Free changes from the second row onward with maturity proceeds, purchases, and a contribution on day 1 of a month. | Should the user manage an independent as-of date and projection start date, or derive one from the other? Confirm how to handle events on the first row and how to determine the local calendar date offline. |
| Holdings range boundaries | `Overview!F1` considers Bank purchases through row 1000. Free uses purchases through row 1518. Bank summary totals end at row 2008, with the fee total starting at row 10. Instrument counts have upper bounds from row 2008 to row 2013. | Are these spreadsheet limits intentional? Approve which holdings should participate in each result and whether the fee total should include row 9. |
| Instrument lookup | `Bank!E9:F9` looks up the instrument in rows 2–7. `E10:F14` starts at row 3. `IFERROR` returns zero for failed lookups and invalid calculations. | Should all purchase lots use the same instrument list? Decide what Fin shows for a missing instrument, zero cost, or invalid term. Confirm whether historical zero results must be reproduced. |
| Fees and yield | `Bank!G9` is an input. `G10:G14` applies a 0.2% fee to price times quantity. `H` includes fees in acquisition cost. `E` annualizes the gain on cost using a 365-day basis, and `E8` weights those yields by expected receipts. | When should the app apply a fee rate or accept an explicit fee? Confirm whether the return basis, day count, and receipt weighting are intentional for both deposits and government bonds. |
| Payout semantics | `Bank!F2:F7` multiplies the instrument price by total quantity. `Overview` adds that payout on the instrument date. The formulas do not describe a separate interim payment schedule. | Confirm how to represent a deposit or bond that pays before maturity, and whether MVP forecasts should include those payments. Decide whether closed or renewed positions need a separate workflow. |
| Returned and formatted totals | `Overview!F2` wraps a Google Sheets text expression with an XLSX fallback. The inner expression converts the formatted `Bank!F8` into an absolute number. `Bank!D8` likewise converts formatted `H8` back to a number for the monthly estimate. | Should the app compute directly from numeric totals? Confirm which total belongs under Returned, the intended treatment of separators and rounding, and whether the XLSX fallback reflects the intended live result. Should the currently blank `Bank!B8` adjustment become an editable field? |
| Overview balances and FX | `Overview!B4` includes converted balances only when `C4` is true. `B5` always includes converted balances and adds Freezed. `F1` uses acquisition cost for qualifying positions. | Confirm the currency roles and quote direction for the two FX inputs, what enabling the `karta` switch means to the user, and whether Total should continue valuing investments at cost. Confirm expected behavior for a missing rate. |
| Inflation series | `Overview!D5` computes Total times the inflation rate divided by 365. Daily `D` discounts the fixed starting Total by elapsed time, while daily `E` subtracts that discounted value from Total. | Confirm the display names and units for `Apr`, daily `Inf`, and summary `Inf`. Should they remain separate measures, and should daily inflation continue to use the fixed Total while Free changes? |
| Charts | The first chart uses Free, Fire, and Apr. The second uses Apr. | Confirm the intended axis scales, labels, date alignment, and how to show blanks. Confirm whether all three series and both charts belong in the first release. |

## MVP product decisions

Review these alongside the [MVP scope](mvp.md) and [domain model](domain-model.md).

| Decision | Question for approval |
| --- | --- |
| Opening state | Which cash and bank balances are entered manually? How should a user record opening holdings and cash so that historical purchases and maturities affect the projection once? |
| Instrument inputs | What security identifier, denomination, and dates are required for bonds and deposits? What does the manually entered `Bank!E2:E7` Target represent, and does it need a feature in the MVP? |
| Forecast inputs | Which FX quotes, monthly contribution, inflation rate, and date inputs may be changed offline? Decide how to store an input's effective date and communicate a stale value, if needed. |
| Local storage and entry | Approve the local entry, backup, and restore workflow. Decide whether historical holdings need a first-release bulk import or can be entered manually. |
| Rounding and verification | Confirm per-currency precision, where rounding occurs, and acceptable comparison tolerances. Preserve the distinction between blank, zero, and calculation error in comparison examples. |

Before approving parity, compare sanitized or synthetic examples for a bond lot with fees, a deposit maturity, a purchase and maturity on the same date, a first-of-month contribution, nonzero inflation, and the Fire formula boundary. Label each approved divergence from Babki in the mapping and record its expected result.
