# Fin MVP specification

Fin is a local, offline-first web application for recording cash and investments and exploring how they may change over time. The MVP has two pages, **Bank** for data entry and holding details and **Overview** for balances and projections.

The [domain model](domain-model.md) defines records, calculation rules, and invariants. [Open questions](open-questions.md) tracks financial policy decisions beyond the baseline.

## Bank

- Enter a dated opening snapshot with manual balances for cash and bank accounts by currency. Include investments already held on that date. Opening balances include the effect of purchases and deposit placements through the snapshot date.
- Add and edit Ukrainian government bonds and deposits. Capture ISIN, currency, denomination, purchase and maturity dates, unit purchase price, integer quantity, fees, and a coupon schedule when applicable for each bond lot. Capture bank, currency, principal, opening and maturity dates, interest rate, and payment schedule for each deposit.
- Show holdings with their acquisition cost, upcoming payments, maturity proceeds, and expected gain after entered fees when the acquisition and payment data is complete. Identify coupon, interest, and principal payments distinctly. A principal repayment is a cash receipt and a reduction in the position's remaining invested cost.
- Show an optional target annual yield for comparison alongside the instrument terms. Editing the target leaves all cash flows and forecast results unchanged.

## Overview

- Show cash and bank balances by currency, their converted total, and the remaining invested cost of holdings. Label the combined asset estimate as cost-based. Label contractual payments as scheduled.
- Let the user edit all forecast inputs offline. The inputs are FX observations, monthly contribution, annual inflation, explicit projection start date, a horizon up to 50 years, and an assumed annual return on available cash. Default the reporting currency to UAH. Show an FX warning when an observation is more than seven days old.
- Show dated projections for available cash, modeled return on cash, nominal projected assets, and inflation-adjusted projected assets. Available cash includes scheduled movements and contributions. Show modeled return separately so it can be distinguished from payments from a holding. Inflation-adjusted amounts express projected assets in opening-date purchasing power.
- Plot changes in available cash and asset values over time using the same dated data as the projection table. A missing or invalid calculation appears as unavailable and remains distinct from a result of zero.

## Local operation and scope

Fin stores manually entered data in local SQLite. Users can reopen the app, edit data, calculate a projection, and explicitly back up or restore the dataset offline. Financial data stays on the device during normal operation.

Bulk import, automatic market data and FX fetching, bank feeds, cloud sync, collaboration, and market-price valuation are outside the MVP. The forecast uses contractual payment schedules and user-entered assumptions.

## Acceptance criteria

1. Enter balances, a bond lot with a coupon schedule, and a deposit with a payment schedule while offline. Reopen Fin and verify the saved inputs. Back up the dataset, change it, and restore the backup to verify the original inputs and record links.
2. With a closing cash balance of 1,000 UAH, purchases on or before the snapshot date leave that opening amount unchanged. A later purchase costing 120 UAH including fees and a scheduled coupon of 20 UAH produce available cash of 900 UAH after both events, with zero contributions. Both events are reflected on the first visible date if they occurred after the snapshot and on or before the projection start.
3. For a holding paid at maturity, recognize its scheduled receipt once and release its remaining invested cost. For scheduled partial principal payments, verify the proportional cost release and the final zero cost. Verify that same-day interest and principal are distinct amounts and that contributions change available cash without appearing as investment income.
4. Use the explicitly selected projection start and enforce the 50-year horizon. At zero assumed return, projected assets equal available cash plus remaining invested cost. At zero inflation, real and nominal projected asset values match. Changing the return assumption affects modeled growth and projected assets without editing contractual payments.
5. At the same viewing date, a seven-day-old FX observation produces no warning and an eight-day-old observation produces a warning. Missing required FX data leaves affected converted amounts unavailable. A complete forecast respects the selected FX inputs without fetching updates.
6. Verify integer bond quantities, cash flow rounding, two-decimal presentation for UAH, USD, and EUR, and distinct missing, zero, and error states. Use exact assertions for deterministic Decimal results and an explicit tolerance for any rounded or approximate calculation. Verify the charts match the dated projection data.
