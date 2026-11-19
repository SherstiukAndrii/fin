# Fin MVP

This specification incorporates the approved MVP decisions. The remaining workbook and calculation decisions are tracked in [open questions](open-questions.md).

## Goal

A single user can run Fin as a local, offline-first web application, record cash and bank balances and holdings in deposits and Ukrainian government bonds, and see how those positions affect available cash and purchasing power over time. The MVP covers the `Bank` and `Overview` workflows.

## Workbook reference

`Bank` calculates purchase costs, maturity proceeds, and a portfolio yield. `Overview` combines balances, FX rates, and active investment costs in a summary and projects available cash, modeled returns, and purchasing power in a daily table and two charts. The mapping records the original formulas, including the late `Fire` formula switch and the undated trailing `Free` result. [Babki mapping](babki-mapping.md)

## Approved workflows

| Workflow | User action | MVP result |
| --- | --- | --- |
| Enter the opening state | Enter a dated snapshot of cash and bank balances by currency and include existing investment holdings. | Opening cash already reflects purchases on or before the snapshot date. Such purchases are never deducted again. Purchases after that date reduce projected available cash on their purchase date. Future payments from opening holdings remain eligible for the projection. |
| Maintain government bonds | For each Ukrainian government bond holding, enter ISIN, currency, denomination, purchase and maturity dates, unit purchase price, integer quantity, fees, and an explicit coupon schedule where applicable. | Bank tracks each lot's acquisition cost and its scheduled coupon and maturity payments. Multiple lots can share an ISIN. |
| Maintain deposits | Enter the bank, currency, principal, opening and maturity dates, interest rate, and payment schedule. | Bank tracks the opening outflow and the scheduled interest and principal receipts. |
| Compare yields | Optionally enter `target_annual_yield` from `Bank!E2:E7`. | Show the target for comparison. Changes to the target leave contractual and projected cashflows unaffected. |
| Set forecast assumptions | Edit FX rates and observation dates, the monthly contribution, the inflation rate, the explicit projection start date, the horizon up to 50 years, and the assumed return on free cash. All inputs are editable offline. | Overview defaults to UAH for reporting. Rates are entered manually, with a warning when an observation is more than seven days old. The free-cash return is a forecast input separate from the investment yield used for comparisons and from contractual payments. |
| Review the projection | Review a dated financial summary, daily table, and charts. | Overview shows balances, holdings, `Total`, `Free`, `Fire`, and the inflation measures according to the agreed rules. The combined `Free`, `Fire`, and `Apr` chart and the separate `Apr` chart draw from the daily series. The choice of `Fire` recurrence and other workbook exceptions remains under review. |
| Resume or recover offline | Reopen the app, create a backup on demand, or select a backup to restore. | The app saves inputs locally in SQLite and supports explicit backup and restore with the network disconnected. |

## Proposed calculation conventions

- A snapshot represents the closing state on its date. Start the displayed projection on or after that date. Carry eligible activity after the snapshot through the first displayed date even when the start is later than the snapshot. Continue the workbook’s first-of-month contribution schedule. Count each purchase, payment, or contribution once.
- Use a horizon measured in calendar years from the selected projection start date, with a 50-year maximum. Compare the daily outputs using explicit dates.
- Show FX age relative to the current local date when the forecast is viewed. A rate exactly seven days old does not trigger the warning. Keep the quoted rates fixed across a projection until the user edits them.
- Use Decimal for monetary calculations and retain intermediate precision. Apply the proposed half-up rule to each final contractual cashflow in its native currency and separately when presenting a value. Display UAH, USD, and EUR with two decimal places. The [domain model](domain-model.md) defines the rounding boundaries and distinguishes missing values, zero values, and calculation errors.

## MVP boundaries

- One local dataset with manual entry and editing. Starting the app, persisting data, running calculations, and backing up or restoring data work without required cloud services.
- Bulk import, automatic market data or FX fetching, bank feeds, syncing, collaboration, and workflows from other workbook tabs remain outside the MVP. Keep spreadsheet exports as references for parity checks.

## Acceptance criteria

1. With the network disconnected, save an opening snapshot, a government bond lot, and a deposit. Reopen Fin and verify the saved inputs. Create a backup, change the dataset, and verify that an explicit restore recovers the backed-up data.
2. With purchases before, on, and after the snapshot date, verify that only later purchases debit projected cash. Include a projection start after the snapshot, a purchase in the intervening period, and events on the first displayed day. Verify the bond coupon and redemption and the deposit interest and repayment are each counted once.
3. Verify that the forecast starts on the entered date and enforces the 50-year limit. Change each assumption and verify its dependent output. Changing the target yield leaves cashflows unchanged. Verify a stale FX warning appears after seven days without any data fetch.
4. Verify integer bond quantities, settlement rounding, two-decimal presentation, and separate representations for missing values, zeros, and calculation errors. Use exact assertions where deterministic Decimal arithmetic permits them. Document any tolerance used for a Google Sheets comparison.
5. Verify the approved Overview totals, daily series, and chart points with sanitized inputs and expected results. Record any intentional difference from Babki and the disposition of the final undated row.
