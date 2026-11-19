# Fin MVP

**Status:** Review draft based on the supplied Babki workbook. The calculation decisions in [open questions](open-questions.md) require approval before implementation.

## Goal

A single user can open Fin locally, update cash and bank balances, record deposits and Ukrainian government bonds, and see how those positions affect available funds and purchasing power over time. The MVP covers the `Bank` and `Overview` workflows.

## Workbook behavior

- `Bank` separates named instruments and their terms from individual purchases. It calculates purchase cost, maturity proceeds, and portfolio yield used by `Overview`.
- `Overview` combines manually entered balances and exchange rates with the acquisition cost of active holdings. Its liquidity starting amount uses a toggle for foreign-currency balances. Its daily projection applies purchases, maturity proceeds, and monthly contributions.
- `Overview` also projects modeled returns and the purchasing power of the starting total. It includes a daily table and two line charts. The exported formulas change behavior near the end of the `Fire` series and include a trailing formula with no date. [Babki mapping](babki-mapping.md)

## Proposed workflows

| Workflow | User action | MVP result |
| --- | --- | --- |
| Maintain instruments and purchases | Add or edit a deposit or bond and enter its maturity terms. Record each purchase with the applicable date, price, quantity, and fee. | Bank lists instrument terms and purchase lots, with calculated costs and projected maturity proceeds. It shows the portfolio yield with the approved weighting and date rules. |
| Set the opening balances | Enter cash and bank amounts in UAH, USD, or EUR. Set the two manual FX rates and choose whether converted balances enter opening liquidity. | Overview shows the opening liquidity amount, `Total`, and the investment summary. The effect of the FX toggle is visible in opening liquidity. |
| Run the daily projection | Enter a projection start date, monthly contribution, and annual inflation. | The daily table shows `Date`, `Free`, `Fire`, `Apr`, and `Inf`. Show the combined `Free`/`Fire`/`Apr` line chart and the separate `Apr` chart from the same dated series as the table. |
| Resume offline | Close and reopen the local app without a network connection. | Saved financial inputs are available and approved calculations run locally. |

## MVP boundaries

- Keep one local dataset with manual entry and editing. The app can start, save, and calculate with the network disconnected. Runtime use requires no sign-in, cloud storage, or spreadsheet connection.
- Leave workbook import and export, bank feeds, live FX and market data, syncing, collaboration, transaction ledger, and other workbook tabs outside the initial scope. The XLSX serves as a reference for the specification and parity checks.
- Use the mapped workbook formulas as the basis for review. Approve any deviation explicitly. Proposed handling of absent required inputs is to identify the missing input and show the affected calculation as unavailable. Preserve meaningful zeros and worksheet blanks where the approved rules require them.

## Proposed changes requiring approval

- Use stable instrument IDs in local data. Confirm whether the lookup rules should still differ for deposit and bond purchases.
- Use the early `Fire` recurrence for all dated rows and end the daily table and charts on the last dated row. The workbook instead switches formulas for the final five `Fire` values and contains one undated `Free` result.
- Calculate `Returned` and the Bank monthly estimate from numeric totals. Confirm the intended aggregation and where to round because the original calculations consume formatted text.

## Acceptance criteria

1. With the network disconnected, enter and edit balances, an instrument, and its purchase lots. Reopen Fin and confirm the saved inputs and calculations are available.
2. Reproduce an approved deposit and bond fixture with separate purchases. Compare purchase costs, maturity cash, and portfolio yield to approved expected values. Cover the purchase and maturity boundaries, including the day immediately before and after maturity.
3. Compare `Total`, opening liquidity, daily estimates, and daily projection rows against approved expected results using sanitized data. Cover both FX-toggle states, start-day behavior, a first-of-month contribution, days with purchases and maturity proceeds, and the approved `Fire` and inflation rules.
4. Change a source input and confirm dependent outputs recalculate. Compare chart points to the relevant table rows, including the approved behavior at the end of the projection.
5. Keep a reproducible fixture with explicit calculation and projection dates, rates, and expected results. Record the approved handling of blanks, errors, precision, and presentation.

See [domain model](domain-model.md) for the proposed records and [open questions](open-questions.md) for review decisions.
