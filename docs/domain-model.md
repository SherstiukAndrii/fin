# Fin MVP domain model

Bank describes deposits, government bonds and their expected payments. Overview combines a dated opening state with investment cash movements and forecast assumptions. This model defines the financial behavior behind both pages.

## Records

| Record | Fields and relationships |
| --- | --- |
| Account | A stable identifier, name, cash or bank category, and currency. A snapshot stores the closing balance for each included account. |
| Opening snapshot | A calendar date, manually entered account balances in their native currencies, and the bond lots and deposits held at the close of that date. |
| Government bond | An instrument identifier, ISIN, currency, denomination, maturity date and an optional target annual yield (`target_annual_yield`). Multiple purchase lots can reference one instrument. |
| Bond lot | A stable identifier, bond reference, purchase date, unit purchase price, integer quantity, fees, and a coupon schedule where applicable. Currency, denomination and maturity date come from the instrument. The lot identifies which payments are due to that position. |
| Deposit | A stable identifier, bank, currency, principal, opening and maturity dates, interest rate, an explicit payment schedule, and an optional target annual yield. |
| Scheduled payment | A stable identifier, bond lot or deposit reference, payment date, native-currency amount and purpose. Purposes include coupon, interest, and principal repayment or redemption. |
| FX observation | Base currency, quote currency, quote units per base unit, and observation date. The forecast uses an explicitly selected set of manually entered rates. |
| Projection inputs | Opening snapshot reference, explicit start date, horizon capped at 50 years, reporting currency defaulting to UAH, selected FX observations, monthly contribution, annual inflation rate and assumed annual cash return. |

SQLite holds the records locally. Manual entry, backup, and restore preserve monetary precision and relationships. Target annual yield is comparison metadata. Contractual payments come from the deposit and bond terms.

## Dated cash movements

The opening snapshot is a closing state. Purchases and deposit openings on or before its date are already reflected in opening cash. Their acquisition costs are carried as the basis of existing holdings. Payments scheduled after the snapshot still affect the forecast on their respective dates.

The cash projection uses these dated movements.

| Event | Date | Effect in native currency |
| --- | --- | --- |
| Bond purchase | Purchase date | Debit unit purchase price × integer quantity + entered fees. |
| Deposit opening | Opening date | Debit principal. |
| Bond coupon or deposit interest | Scheduled payment date | Credit the scheduled amount for the position. |
| Principal repayment or redemption | Scheduled payment date | Credit the scheduled principal or redemption amount. A final repayment or redemption releases the position’s remaining invested cost. |
| Monthly contribution | First calendar day of each month after the snapshot | Add the scenario contribution in its entered currency. |

Keep each movement individually identifiable even when several movements settle on one day. Scheduled amounts are entered as positive native-currency amounts for the whole position. The movement type determines whether cash increases or decreases. Use the explicit principal schedule when provided. For a holding with one repayment at maturity, use bond denomination times lot quantity or deposit principal as the scheduled receipt. Recognize that repayment once. The forecast assumes payments follow the entered terms.

The start date must fall on or after the snapshot date. Accumulate all post-snapshot events through each displayed closing date, including any activity on a first displayed date later than the snapshot. Measure the 50-year horizon limit in calendar years from the projection start. Keep the same dated points in the daily table and charts.

## Reporting and valuation

Use the selected FX quotes throughout a projection. For a quote expressed as reporting units per unit of native currency, multiply the native amount by the quote. For native and reporting currencies that match, use a conversion factor of one. Warn when a selected quote is more than seven calendar days old on the day the user views the forecast. An observation dated after the viewing date is ineligible under the proposed rule. Keep native amounts visible when a missing or invalid quote makes a reporting-currency aggregate unavailable.

Let `s` be the snapshot date, `d` the closing date of a projection point, and `p` the immediately preceding calendar date. All amounts below are in the reporting currency. Use the following baseline calculation conventions, including on days before a later display start.

| Measure | Calculation |
| --- | --- |
| Projected available cash | On `s`, sum the converted account balances. On each later date, add the converted, signed movements on `d` to available cash on `p`. |
| Cumulative modeled cash return | Start at zero on `s`. For each subsequent day, add the daily return on the positive portion of prior-day scenario cash. With effective annual return assumption `r`, the daily rate is `(1 + r)^(1/365) − 1`. |
| Scenario cash | Available cash plus cumulative modeled cash return on the same date. The modeled return remains a scenario result. |
| Remaining invested cost | Sum the acquisition cost still attributed to holdings at the close of `d`. Use the finalized acquisition debit as the cost basis, including bond fees. Allocate a holding’s acquisition cost across its scheduled principal or redemption receipts in proportion to their amounts. A principal receipt releases its allocated cost. Coupons and interest leave the cost unchanged. Release any residual cost at the final principal receipt. For an opening position, use the cost remaining after principal receipts through the snapshot date. |
| Projected assets | Scenario cash plus remaining invested cost. Use acquisition cost as the valuation basis for holdings. |
| Inflation-adjusted projected assets | Divide projected assets on `d` by `(1 + i)^(elapsedDays/365)`, where `i` is effective annual inflation and `elapsedDays` counts calendar days from `s` through `d`, excluding `s`. Express the result in snapshot-date purchasing power. |

The return and inflation assumptions enter as decimal rates greater than −1 under this proposed effective-rate model. Projected available cash may be negative and exposes a funding shortfall. Modeled return uses the prior closing balance, so movements on a date affect the return base from the following day.

Bank shows holding acquisition costs, dated payments, and expected gain after entered fees. Expected gain equals the sum of scheduled coupon, interest, and principal receipts over the holding’s life minus its acquisition cost, in the holding currency. Calculate it when the schedule and cost are complete. A method for annualizing investment yields across irregular payment schedules remains open.

## Precision and data integrity

- Use Decimal for money and retain intermediate precision. For UAH, USD and EUR, the proposed rule posts each finalized native-currency movement at two decimals using `ROUND_HALF_UP`. Apply the same mode separately when displaying UAH, USD or EUR values to two decimals.
- Preserve distinct states for a missing amount, an entered zero, and a calculation error. An unavailable input makes dependent results unavailable. Preserve the original amounts and dates for review.
- Require each payment to reference a valid holding and each lot to reference a valid instrument. Preserve multiple purchases of the same instrument as separate lots. Apply each post-snapshot movement once.
- Use exact assertions where deterministic Decimal arithmetic permits. State the error tolerance in forecast tests involving fractional powers. Compare before presentation rounding.

See [open questions](open-questions.md) for extensions to the scheduled-payment model.
