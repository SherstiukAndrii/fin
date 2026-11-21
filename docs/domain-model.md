# Fin MVP domain model

This document defines the financial records, relationships, calculation rules and invariants used by **Bank** and **Overview**. It is independent of spreadsheet cells or workbook formulas. See the [MVP specification](mvp.md) for behavior and acceptance scenarios, and [implementation details](open-questions.md) for parameters not yet specified.

All product choices **Q01–Q16 are approved**. Details labeled *implementation choice* below must not silently override them.

## 1. Fundamental records

| Record | Required meaning |
| --- | --- |
| Currency and Money | Currency identifier and exact `Decimal` amount. Currency conversion requires a dated rate; money in different currencies must not be added directly. |
| Account | Stable ID, name, cash or bank account category, native currency, accessible/unavailable balance information. |
| Opening snapshot | Selected calendar date, per-account closing balances, existing investment positions (with remaining cost basis and quantity), and outstanding liabilities. History before this date is not replayed into cash. |
| Monthly control snapshot | Date and observed account/portfolio balances. Compare with calculated values; differences generate explicit, auditable correction events. It does not silently replace earlier entries. |
| Bond instrument | Identifier/ISIN, denomination, currency, maturity and contract terms. An instrument may have **multiple dated coupon obligations before maturity** and, where applicable, partial principal repayments. Many lots may reference one instrument. |
| Bond lot | Instrument reference, acquisition date, integer quantity, unit price, paid fees, acquisition currency/cost basis and remaining quantity. It is entitled to **every applicable dated coupon** and applicable principal payments for its quantity. Do not assume one coupon only or a single maturity cash flow. |
| Deposit | Stable ID, bank, currency, original principal, dates, interest terms, payment schedule, and remaining principal/cost basis. |
| Liability | Stable ID, creditor/type, native currency, remaining principal and obligations accrued as of a dated assessment, with any separately planned future payments. |
| Payment obligation | Stable ID linked to a holding, expected date, amount, currency and type: coupon, interest, principal or redemption. **One holding has many obligations**, e.g. several coupon dates and a separate maturity/redemption. Record whether a quoted amount is per bond/unit or for the entire lot; materialize the payable lot amount exactly once. |
| Actual receipt | Stable ID linked to a payment obligation, actual date and amount/currency. Many partial receipts may settle one obligation; unreceived balance remains expected/overdue. |
| Dated event | Stable ID, effective date, type, origin, native currency/amount, affected accounts and/or holdings. Includes purchases, contributions, transfers, repayments, realized trades, corrections and one-off cash flows. |
| Price observation | Asset ID, value, currency/unit, observation date, source (manual in MVP), optional explanatory note. Purchase cost is separate. |
| FX observation | Base currency, quote currency, quote units per base unit, observation date, source. Store a history, not one mutable exchange rate. |
| Liquidity classification | Exactly one incremental tier 0, 1, 2 or 3 for each eligible asset or portion, plus estimated exit costs and time-to-cash where applicable. |
| Expense/tax observation | Position/event reference, kind, currency, amount or explicit unknown/not-applicable. Zero is a confirmed value, not a missing one. |
| Contribution schedule | Native-currency net monthly contribution with effective-from date; successive changes replace the prior scheduled amount. |
| One-off forecast event | Future date, currency, signed amount and origin; forecast-only unless confirmed as an actual event. |
| Forecast assumptions | Projection start date, horizon up to 50 calendar years, reporting currency default UAH, one annual inflation rate in MVP, future FX assumption, and any separately labeled *manual scenario* rate. |
| Optional comparison yield | `target_annual_yield` is metadata for comparing holdings; never generates contractual cash flows by itself. |

SQLite stores local data and supports backup/restore. Records use stable IDs, explicit dates and provenance. Automatic price feeds and imports are future adapters and do not change calculation semantics.

**Investment calculator boundary:** shared Money/Decimal, currency, dated CashFlow, Instrument, Position/Lot and valuation data must be reusable by a separate `CalculatorService`. Reserve strategy-style engines for bond/OVGZ, equity/ETF, deposit and generic cash-flow scenarios, plus provider-neutral ports for instrument metadata, payment schedules, quotes, FX and corporate actions. A future NBU OVGZ schedule adapter may populate the instrument's dated coupons/principal events, but must not overwrite actual receipts or historical purchase prices. Scenario results are never automatically posted to the ledger. See [Investment Calculators design](investment-calculators.md).

**Future extension boundary — bank statements.** Core ledger, account snapshots, holdings and real settlement events must remain independent of bank export formats. A later Statement Analysis module introduces its own import batch, normalized bank transaction, category, statement-to-ledger match, analytical rule and explainable finding. These are **read-only analytical/staging records**, never automatically authoritative transactions. Matching a bank transfer, refund, bond coupon or deposit interest must prevent double booking. The core can expose stable Account/Event/Payment IDs and read-only repositories so statement adapters attach later without changes to Free, Fire or Total formulas. See [Statement Analysis design](statement-analysis.md).

## 2. Opening state, accounting and reconciliation

The **opening snapshot is a closing state at date `s`**. Acquisition events at or before `s` have already affected opening cash and holdings; do not replay their debits. For each subsequent event, apply its effect **once** on its effective date. A transfer between own accounts changes account allocations but does not create new external income or increase Net Worth.

Maintain separate ledgers/derived views:

1. **Actual cash:** opening balances, confirmed post-snapshot movements, actual receipts and explicitly recorded corrections. Scheduled but unpaid income does not enter actual cash.
2. **Expected cash:** baseline for a future date using contractual scheduled events, effective contribution schedules and one-off planned flows. When an actual receipt settles all or part of an expected payment, reconcile it with that obligation; do not add two copies of the same amount.
3. **Portfolio and liabilities:** position quantities/values and the outstanding obligations. A purchase exchanges cash for a holding; only fees, price differences or other changes in Net Worth affect wealth, not the whole invested principal.

Monthly control snapshots serve as verification. A discrepancy is shown before an auditable correction event is created; do not rewrite history or label a balance correction as investment income. Different events may share a date, but each retains identity and purpose. Exact within-day ordering is an implementation detail.

## 3. Holdings and financial flows

| Event | Actual cash | Forecast cash | Holding/liability effect |
| --- | --- | --- | --- |
| Bond purchase after opening date | Debit purchase amount and paid fees when confirmed | Debit if scheduled | Increase purchased lot and its cost basis |
| Deposit opening after opening date | Debit deposited principal when confirmed | Debit if scheduled | Create/increase deposited principal |
| Expected coupon/interest | No change until receipt | Credit each independently scheduled coupon/interest at its own date; **several coupons can precede maturity** | No principal or acquisition-cost reduction; no change in bond quantity |
| Partial actual coupon/interest | Credit amount actually received on actual date | Reconcile with scheduled obligation | Outstanding expected amount decreases; remainder may be overdue |
| Principal repayment/redemption | Credit actual receipt when received | Credit scheduled principal on expected date | Decrease outstanding principal/quantity/cost basis; avoid double-counting redemption as investment income |
| Sale of investment | Credit realized net proceeds when settled | Only if explicitly planned | Reduce position; realized result comes from net proceeds minus attributed cost |
| Monthly net contribution | Only when actual inflow is recorded | Apply amount from effective-date schedule | External contribution, not investment return |
| One-off expense/income | Actual only upon confirmation | Planned amount on scheduled date | Change cash, not investment yield by default |
| Liability payment | Debit when paid | Planned future debit if scheduled | Reduce principal/accrual as allocated, with interest/charges separately |
| Own-account transfer | Debit and credit own accounts | Same if planned | No new wealth |

**Multiple coupon obligations:** A single OVGZ lot can have zero, one, or many coupon payment dates before its maturity. Maintain a separate schedule entry for each payment, including amount, currency, payment type, date and link to the instrument/lot. The instrument schedule may be per security, while the lot's claim multiplies a per-security amount by its eligible quantity; prevent applying that multiplication twice. A coupon changes forecast cash on its scheduled date (or actual cash only on receipt), and never releases the bond's principal or acquisition cost. Amortizing principal is a different event type; it reduces outstanding principal and releases the corresponding cost basis. A final redemption is separate and occurs once.

**Test illustration:** Holding **2 OVGZ** of **1,000 UAH nominal each**, with **40 UAH coupons per bond** due **March 1, June 1 and September 1**, and **principal redemption December 1**, produces three distinct scheduled coupon entries of **80 UAH** each (**240 UAH total**), plus a single **2,000 UAH principal** event. The three coupons do not reduce principal; actual receipts may occur late or partially. No additional coupon is inferred on the maturity date unless the schedule explicitly includes one.

After a holding matures, its returned principal remains cash until the user **explicitly** creates a reinvestment transaction; do not roll it into a new instrument automatically.

**Principal and cost basis:** keep acquisition cost for realized-gain calculations and the Fire denominator. For installment principal repayments, release the corresponding part of cost basis and settle any residual on final redemption using a documented method. This remaining cost is **not** the current price used by Total. Include coupons, fees, taxes and accrued interest in their proper categories; do not silently count them twice.

**Partial payments:** an obligation may have actual receipts of multiple dates and amounts. Its remaining expected amount is the scheduled amount less the amounts already applied. Due and unpaid amounts are marked overdue. If actual receipts exceed or differ from the schedule, preserve both and require a reconciliation explanation; do not manufacture a second scheduled payment.

## 4. Valuation, FX and liquidity

### Total and valuations

For an evaluation date `d` and chosen reporting currency:

`Total_nominal(d) = sum(current_or_last_known_asset_values(d)) - sum(outstanding_principal_and_accrued_liabilities(d))`

Convert each component into the reporting currency **at date `d`**, retaining underlying native amounts. Default report currency is UAH; USD and EUR switches change the display only. Keep Assets, Liabilities and resulting Total separately inspectable. The initial purchase price is historical cost metadata, not today's valuation. The asset value may be a direct account balance, market quotation, or dated manual appraisal. Show its date, source and any asset-class-specific staleness warning.

Never double-count the same value as an investment and a hypothetical cash sale. For a bond/deposit, ensure principal, accrued interest, market quotation (clean/dirty where relevant) and received coupons are handled without duplication. The exact pricing convention for some instruments is still an implementation detail.

For an FX pair with `base`, `quote` and `units_per_base`, conversion follows its stored direction; inverse conversion is mathematically explicit. Historical valuation at `d` uses the latest observation dated **no later than `d`**. Age **greater than 7 calendar days** at `d` causes a warning but still allows conversion. If no eligible FX path exists, do not substitute zero: keep native totals and mark converted Total **incomplete**.

For a date *after the latest observed rates*, the future forecast holds those rates constant **as an explicit scenario assumption**, with a clear caution that this is not an exchange-rate prediction. FX observations never silently mutate past valuations.

### Free (liquidity)

Compute `Free(level, d)` as the converted sum of assets assigned to tiers `0..level`, with each asset portion counted **once**. The tiers are cumulative:

| Tier | Incremental assets |
| --- | --- |
| 0 | Unblocked cash and directly usable bank balances |
| 1 | Other super-liquid items, e.g. rapidly exchangeable currency |
| 2 | Saleable stocks, ETFs, government bonds and similar securities |
| 3 | Less liquid but saleable property (real estate, vehicles, other possessions) |

Tier classification is about **accessibility**, not asset currency or double-counting the same account in cash and FX. For items requiring liquidation, use **estimated net sale proceeds** after known commissions, taxes, spread and exit costs, with the estimated delay visible. Unknown costs are **not assumed to be zero**; display the uncertainty. Tier 3 is not equivalent to immediately spendable cash. A missing required FX rate makes affected converted Free incomplete under the same FX integrity principle.

## 5. Fire — realized historical yield, not booked cash

Fire is a **hypothetical income metric**:

`Fire_year(d, level) = Free(level, d) * realized_annual_yield(window, d)`

`Fire_month_indicative = Fire_year / 12` is an indicative monthly amount, not a promised monthly coupon.

Eligible realized results: cash **actually received** as coupons, dividends and interest, plus gains/losses on sales and closed operations measured against attributed acquisition cost, **net of applicable known commissions and taxes**. Exclude unrealized mark-to-market changes and returned principal. An interest receipt from an open holding can still be realized income.

Compute the realized yield from **net realized income over the chosen window divided by the daily average invested capital at cost**, accounting for purchases, sales and principal repayments. The standard window is 12 months; the user may select **3, 6, 12 months or all history**. For less than 12 months of available history, annualize using the observed interval and show a conspicuous warning. Use an annualized rate for the Fire multiplication; the precise day-count formula (including leap years) and cross-currency portfolio basis must be specified before implementing this calculation.

If there is no reliable invested capital or income history, the actual yield and Fire show **“no data”** rather than 0%. A verified zero result over a known capital base can produce a zero yield. A user-entered rate may drive a **separate manual scenario** but must never overwrite the actual realized yield.

Fire is **not a claim about expected future market returns** and is **not added** to actual cash, future contractual payments or Total. If selected Free already contains investments, Fire represents a hypothetical alternative yield on that selected amount, **not extra profit on top of the same holdings**.

## 6. Forecast, inflation and reporting

Let `t` be the user-selected **projection start** date (which may be later than opening snapshot date `s`). Accumulate actual/expected events after `s` up through `t` before displaying results; never silently drop events between them. The user can forecast up to **50 calendar years from `t`**. The full-range presentation is **monthly**, with daily detail for shorter intervals; calculations can use exact event dates internally.

Apply the active **effective-date net monthly contribution** plus one-off planned events. An external contribution adds forecast capital but does not create investment income. Forecast contractual receipts based on schedules, reconciled against known actual receipts. Do **not** silently reinvest redeemed principal. Future market prices are the last available estimate unless a future scenario is explicitly defined; show that an assumption is being used.

Define for reporting currency `c`:

- `Total_nominal(d,c)`: dated asset values minus dated outstanding liabilities.
- `Total_real(t,c) = Total_nominal(t,c)` because the base-date price index is `1`.
- `Total_real(d,c) = Total_nominal(d,c) / inflation_index(t,d)` for subsequent forecast dates.
- In the MVP, one manually entered effective annual inflation rate applies across the horizon; compound inflation rather than subtracting a flat percentage each year. Design the index function to accept per-year rates in a later version.

Display **Total Nominal and Total Real side by side**; they are views of one capital measure, not two assets to add. Inflation changes purchasing-power presentation, not nominal transaction cash or the current nominal Total. Keep all financial amounts in native currency until they are converted for display/reporting. A missing valuation or FX observation should propagate **unavailable/incomplete** status instead of false precision.

## 7. Precision, integrity and offline behavior

- Use exact `Decimal` monetary inputs and math. Round only when posting a finalized real movement under currency/instrument rules or formatting a displayed value. Do not use binary `float` to persist monetary amounts. Distinguish **confirmed zero**, **unknown/not provided**, **not applicable**, and **calculation failure**.
- Store the **source and observation date for each price and FX value**. In the MVP all observations and terms are manual. Optional official-source adapters may later supply data without replacing historical observations or breaking offline calculations. Price-staleness thresholds vary by asset class and will be configured when the relevant pricing module is implemented.
- Preserve immutable event IDs, valid references and histories; prevent duplicate application of cash flows. A snapshot and its corrections must be independently inspectable.
- Back up SQLite regularly to local storage, permit manual export and restoration, and test integrity after restoration. Bind the web process to localhost by default; VPN access and authentication are later work.
- Tests should cover currencies, snapshots, post-snapshot purchase/deposit debits, **multiple coupons before an OVGZ's redemption and independent coupon/principal schedules**, scheduled/partial/late receipts, principal-versus-profit separation, no automatic reinvestment, Free tiers and selling costs, dated FX/staleness, missing values, Fire periods, real-versus-nominal values and monthly reconciliation.

Unresolved **technical parameters** (not unapproved Q01–Q16 choices) are tracked in [implementation details](open-questions.md).

## 8. Banks, monetary accounts and securities custody (SQLite v6)

A **Bank** is an institution rather than a money balance. The same bank
may own multiple bank accounts even when their currencies are identical.
Money accounts have their own IDs and exact per-currency balances; physical
cash locations never belong to a bank. A **SecuritiesAccount** belongs to a
bank but is a custody address with no cash balance or automatic security
valuation. A custody entry is not evidence of an owned bond and cannot
double count imported positions.

For a new money account whose activation date is `a > s` (opening
snapshot date `s`), define its actual closing balance:

- unavailable for `d < a`, even if `d >= s`;
- zero at `d = a` until an explicitly posted same-day cash movement;
- sum of its eligible, native-currency cash-event legs dated `a <= e <= d`
  for subsequent `d > a`.

Accounts already present at `s` retain their explicit opening amounts.
Adding an account must not insert a new `opening_balances` row, create an
income event or replay an investment purchase. Transfers use two equal and
opposite legs in one currency; no implicit FX. The repository rejects an
event earlier than an account's activation, including when editing an event.
A monthly observation on date `t` includes only accounts active at `t`
and does not retroactively add later accounts to old observations.

The v6 SQLite upgrade is transactional and protected by a private,
pre-upgrade copy. Historic financial amounts remain stored as decimal
strings. Names and HTTPS bank links are metadata, never login credentials.
Deletion protects linked accounts and requires explicit signed review.
