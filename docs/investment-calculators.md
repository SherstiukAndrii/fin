# Investment calculators and optional market-data adapters

**Architecture requirement, not implemented code.** Extend Fin with calculators for **OVGZ/other bonds, stocks and ETFs, deposits, crypto and arbitrary other investments**, with offline/manual operation first and **optional internet data wherever an authorized reliable source exists**. Preserve the original approved Q01–Q16 decisions and separate real account balances, purchased holdings, investment comparisons and hypothetical future scenarios.

Related: [MVP](mvp.md), [domain model](domain-model.md), [remaining implementation details](open-questions.md), [Statement Analysis](statement-analysis.md).

## Product boundaries

- A **calculator** evaluates a prospective or existing position, shows cash-flow schedules, expected net outcomes and sensitivity to assumptions. Running a scenario never books a purchase, sale, coupon or investment-income event in the user's real ledger.
- A **holding** has actual trades, costs, current price/valuation and confirmed payments; Bank and Overview read from the same independent calculation service once transactions are approved.
- A **market data quote** is an observation, not the user's transaction price and not a promise that the market is liquid. Auction yields, dealer indications, midpoint or last price must be labeled accurately. Do not infer executable bid/ask or missing coupon schedules.
- Multiple asset classes share `Money/Decimal`, calendar/date, currency, FX, costs/taxes, scenario inputs and results; keep asset-specific valuation and cash-flow rules separate. No mandatory data feed, bank login, broker integration or Internet connection.

## Proposed architecture

```text
Bank UI / Overview UI / standalone Calculator UI
                       |
                  CalculatorService
                       |
        +--------------+---------------+
        |              |               |
   BondEngine      EquityEngine    GenericCashFlowEngine
 (OVGZ/bonds)     (shares/ETF)    (deposits/other assets)
        |              |               |
        +------+-------+---------------+
               |
      Calendar / Money / FX / fee & tax policy
               |
    Quote & instrument-data ports (read-only)
               |
      Provider-independent observations cache
               |
ManualInput | NBU official | Exchange/broker feed | other API
               |
       SQLite storage (offline-first)
```

Use `Instrument` + instrument-type-specific terms, `Position/Lot`, `InvestmentScenario`, `CashFlow`, `CorporateAction` and `MarketObservation`, all with identifiers and dated provenance. Prefer a small dispatcher/strategy registry keyed by `instrument_type`; do not grow one giant calculator with special-case columns. A generic **dated cash-flow evaluator** can be reused for any instrument, but it must not pretend that every asset has guaranteed payments.

Suggested narrow interfaces (Python protocol or equivalent, allowing future C++ migration):

- `InstrumentMetadataProvider.lookup(identifier) -> DatedSourceResult[InstrumentTerms]`
- `PaymentScheduleProvider.get_schedule(instrument_id, as_of_date) -> DatedSourceResult[list[ContractualPayment]]`
- `MarketQuoteProvider.get_price(instrument_id, as_of_date, quote_kind) -> DatedSourceResult[Quote]`
- `FxRateProvider.get_rate(pair, as_of_date) -> DatedSourceResult[FxObservation]`
- `CorporateActionsProvider.get_actions(symbol, date_range) -> DatedSourceResult[list[CorporateAction]]`
- `Calculator.calculate(terms, inputs, valuation_and_schedule) -> CalculationResult`

These are **architectural contracts, not committed Python code or guaranteed provider API signatures**. An optional adapter may cover only some capabilities; partial availability is a normal result. Providers never call finance engines with unvalidated raw JSON or HTML.

### Core data and source metadata

- `Instrument`: type (government bond, other bond, equity, ETF/fund, deposit, crypto, property/other), stable internal ID, optional ISIN/ticker/contract symbol, exchange/market and denomination currency.
- `InvestmentScenario`: amount/quantity, quoted buy price (clean/dirty or per-unit convention), purchase/settlement date, optional liquidation date or holding horizon, fees/taxes/spread, cash-flow reinvestment **only if explicitly assumed**, FX and inflation assumptions.
- `CashFlow`: obligation date, type (purchase, coupon, dividend, interest, redemption, fee/tax, sale, funding), signed amount, currency, `per_unit` or `total_lot`, expected/actual/scenario status, provenance and occurrence ID.
- `MarketObservation`: instrument ID, source, value, value currency/unit, observation timestamp/timezone, publication date, fetched-at, quote_kind (last, close, bid, ask, NAV, indicative, auction yield, estimated), quality/license notes, optional original source record ID, adapter/parser version and stale/invalid state.
- `ScenarioResult`: invested cost, dated gross/net cash-flow table, net profit, total and annualized return when mathematically defined, realized versus unrealized component, break-even price, current value, risk/assumption labels, selected reporting currency and warning list. Never replace **missing** with zero.

Store all numeric inputs and results using `Decimal`. A formula that uses fractional powers or solves a root numerically must explicitly document method, convergence tolerance and failure states. Amounts in mixed currencies are not aggregated without recorded, date-appropriate FX.

## Calculator A — Ukrainian government bonds (OVGZ) and other fixed-income bonds

Inputs: ISIN or manual terms, settlement and maturity dates, currency, quantity, nominal per bond, quoted clean price versus dirty/full price (if available), accrued coupon interest, trading/bank fees, relevant tax assumptions and **full dated coupon/principal schedule**. Do not hardcode coupons as annual or require only one payment. A bond may have several coupons before maturity, coupon amounts can differ, and some instruments repay principal partially.

Outputs:
1. Purchase cash requirement including accrued interest and paid fees, with quote/basis labeled.
2. Table of each expected coupon/partial principal repayment/redemption (per security and multiplied by position quantity exactly once); total cash receipts and net cash profit after applicable entered costs/taxes.
3. Holding-to-maturity annualized yield to maturity (**YTM/IRR of all dated net flows**, numerically where valid), calendar-weighted annual equivalent and break-even price, with missing/ambiguous-root indicators rather than a fabricated value.
4. For an existing lot: remaining coupons from the assessment date; estimated net sale proceeds vs. hold-to-maturity receipts, accrued coupon and market price shown separately so unrealized appreciation is not realized Fire income.
5. Currency and inflation-adjusted views, with rates and future FX assumptions labeled.

**Important distinction:** the yield on newly purchased bond at today's **actual offered price** is not identical to the original coupon rate, published auction yield or bank's indicative listing. Net yield may vary due to purchase price, commissions, taxes, accrued interest, withholding and payment timing. The calculator must not substitute auction average yield for executable retail quotes.

Example synthetic test: **2 bonds, 1,000 UAH nominal each**, 40 UAH **per bond** on three distinct coupon dates → three **80 UAH** scheduled coupon receipts, **240 UAH** in coupons; final **2,000 UAH** principal repayment separate. All expected flows are independent of actual receipts and of Fire until realized.

## Calculator B — stocks, ETFs and other listed funds

Inputs: ticker + exchange and/or ISIN, trading currency, fractional or whole quantity as the venue supports, trade date/settlement price, commissions, transaction taxes (if any), dividends/distributions, withholding assumptions, historical splits/reverse splits, other corporate actions, current quote/NAV and optional target or scenario price.

Outputs: invested cost and weighted cost per unit; current gross/net liquidation value, realized vs unrealized P&L, received cash dividends (net), hypothetical total return **including reinvestment only when explicitly requested**; annualized holding-period return for dated flows when calculable; dividend yield and scenario sensitivity for future price/FX, without claiming predictive accuracy.

**Corporate action rules:** store raw and split-adjusted prices distinctly, preserve actual quantities/dividend dates and prevent retroactive double application of splits or dividends. For accumulating ETFs, do not fabricate a dividend cash flow; distribution policy must be recorded.

## Calculator C — deposits and other fixed-income contracts

Inputs: principal, currency, quoted annual rate, simple/compound/periodic interest convention, capitalization or cash payout schedule, day-count convention, deposit start/end, early termination penalty, withholding/fees and the actual bank terms.

Outputs: dated interest payments, gross/net earnings, maturity principal, effective annual return and hypothetical early-close amount. Interest paid out to an account is not simultaneously capitalized into the principal; capitalize **only** if terms say so.

## Calculator D — extensible investments (corporate bonds, crypto, property, other)

- Generic `InvestmentCashFlowCalculator`: arbitrary dated investment and withdrawal flows, costs, recurring receipts, sale/exit value, capital movements; estimate net money-weighted return (XIRR-like) only when a meaningful/unique solution exists. Multiple/no roots or inconsistent timing → show undefined or warning.
- For crypto: source/time-stamped market quotes, decimal quantities, trading/network charges, realized/unrealized distinction; price updates are not wallet balances. Property: appraisal provenance, vacancy/maintenance/tax/exit costs and low-liquidity warning; market data may be unavailable or non-comparable.
- No asset class silently guarantees positive future appreciation or coupon-like income where none exists.

## Online data — provider adapters and priority

The following **candidate** sources are grounded in published provider documentation; actual endpoint availability, historical coverage, authentication, rate limits and terms of use **must be checked again at adapter implementation time**. Do not promise a real-time or executable quote.

| Data | Candidate provider | Expected capabilities / constraints |
| --- | --- | --- |
| OVGZ instrument terms and all dated coupons/redemptions | **NBU Depository public OpenData API** `https://bank.gov.ua/depo_securities?json` | Official securities reference includes ISIN, nominal, maturity, `payments[]` with `pay_date`, `pay_type`, `pay_val` per security. Match by ISIN and validate the payment convention before using. |
| OVGZ placement yields and historic auction results | **NBU OVDP auction results** / Ministry of Finance open data | Useful contextual yield/ISIN/maturity dates; auction yield **is not** a retail quote. |
| Historical UAH foreign exchange | **NBU official FX API** `https://bank.gov.ua/NBU_Exchange/exchange_site?...&json` | Date-stamped official rate; not necessarily the executable card/broker FX spread. Keep bought/sold effective rates and bank spread separate. |
| Stocks/ETFs quote history, dividends/splits | **Alpha Vantage** public API `https://www.alphavantage.co/documentation/` or a licensed exchange/broker adapter | May provide time series and corporate actions; requires key, quotas and exchange/entitlement rules. Price can be delayed; distinguish adjusted data from raw prices. |
| Actual retail OVGZ deal quote, broker fees, ETF NAV, deposit terms | Broker/bank official price table or supported authenticated API **only if available and permitted** | May require manual entry. Never replace actual paid settlement amount with public source estimates. |
| Crypto prices / other assets | Dedicated adapter with explicit asset ID, quote currency, venue and timestamp | Provider support varies; allow manual valuation and offline calculations. |

References (provider-level candidate docs, not implementation dependencies):
- NBU Depository API: https://bank.gov.ua/admin_uploads/article/Instr_API_depo_securities.pdf
- NBU exchange-rate API: https://finliteracy.bank.gov.ua/admin_uploads/article/Instr_API_KURS_VAL_data.pdf
- NBU auction result data: https://bank.gov.ua/admin_uploads/article/Instr_Domestic_government_bonds_NEW_API.pdf
- Alpha Vantage documentation: https://www.alphavantage.co/documentation/

## Safe data refresh and offline behavior

- **Manual data always works**. A user initiates refresh; optional automatic caching/scheduling comes later. Cache last successful observations and complete schedules in SQLite, with source URL/provider, adapter version, fetched-at and observation date.
- Expose statuses: fresh / stale / unavailable / invalid / manually overridden; show quote type, rate source and age. Historical calculations always use data dated no later than assessment date, per approved FX rule. Keep deterministic offline snapshots for repeatable tests.
- Validate provider content against schema, ISIN/date/amount/currency and payment-type constraints, reject duplicate coupon IDs, unexpected sign and inconsistent split events. A new feed can suggest updated terms but should **not overwrite user-approved purchase prices or actual receipts**. Changes must be reviewable.
- Protect API credentials; use per-provider quotas/backoff/timeouts, explicit network opt-in, no hidden background requests when offline or disabled. Cache respect provider license and avoid unlicensed redistribution. Missing quote must not block manual scenarios or invent a price.
- Build adapters as interfaces independent of UI and domain; use mocked/provider fixture tests, no live network in CI. Avoid raw HTML scraping unless provider terms allow it and an adapter is explicitly approved.

## Integration with Bank, Overview, Fire and Statement Analysis

- A calculator's scenario has **no balance effect**. Converting an accepted hypothetical scenario into a real holding requires an explicit user action and actual execution data (trade price, date and costs).
- Bank owns actual lots/holding terms/confirmed cash flows; Overview displays current market/appraised values for Total, estimated net liquidation proceeds for Free and realized income only for Fire. Forecast uses schedules and clearly marked assumed future market/FX values; **never add hypothetical yield twice**.
- Statement Analysis can later match a bank-recorded deposit funding, OVGZ coupon, share dividend or trade fee by stable event/obligation ID; its imported transactions do not automatically create a second ledger entry.
- Proposed implementation sequence: **(1) shared Money, FX, dated flows and provider ports, (2) manual OVGZ + deposit calculators and tests, (3) optional NBU OVGZ schedule/FX adapters, (4) stock/ETF calculator and manual quotes, (5) licensed equity/ETF feeds and corporate actions, (6) generic other-investment engine and comparison views.** Scope each as a separate small PR.

## Acceptance examples

1. Synthetic OVGZ with 3 coupon dates + redemption calculates all four entries. Updating the bond market quote changes its *Total valuation* but not historical cost, already received income or actual account cash.
2. A missing coupon schedule, dirty/clean price basis or purchase fee shows an explicit incomplete result for metrics that require it. A user may enter missing fields manually and run the same calculation offline.
3. Import an official schedule with `pay_val` **per bond** and quantity 200: create 200 × each payment amount, not 200 × a lot-level payment again. Redemptions and coupons are separate.
4. Enter 10 shares at 50 and 10 at 70 in the same currency: average purchase price is 60 per share *before trading costs*. An unrealized rise to 75 is not realized Fire income. Realized dividends count only after actual receipt.
5. Recalculate a historical date with only a later FX quote available: conversion remains unavailable, not silently backfilled from the future. Offline cached data is usable according to the stored observation date.
6. Compute alternative bonds/ETFs/deposits in separate currencies: display side-by-side original-currency outcomes and explicitly labeled FX scenario; do not silently rank by incomparable nominal percent figures or presumed future FX.
