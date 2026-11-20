# Fin MVP implementation details and remaining technical questions

**All 16 product choices Q01–Q16 are approved.** They are documented in the [MVP specification](mvp.md), [domain model](domain-model.md) and the Fin Google Doc. None of the options Q01–Q16 should be reopened as an undecided product requirement.

This file tracks **technical conventions and edge cases** to decide in the small implementation PR where they matter. Do not silently choose a convention that contradicts the approved model.

## Before implementing the relevant calculation

| Module | Technical detail to settle | Constraint already approved |
| --- | --- | --- |
| Fire: calendar normalization | Exact annualization with actual calendar days, leap years and a selected 3/6/12-month/all-history window. Decide how zero-event periods are distinguished from unknown history. | Realized net income / daily-average invested cost; annualize short history with warning; missing reliable data is not zero. |
| Fire: multiple currencies | How to value historical investment income and cost basis when transactions were in different currencies; document FX date convention and missing-rate behavior. | No invented profits, no unobserved FX rates, no asset revaluation in realized returns. |
| Deposit/bond valuation | Define instrument-specific dirty/clean price, accrued interest, estimate fallback, acquisition fees and the release of cost basis on partial principal repayments. | Total uses dated market/estimated value; purchase cost is separate; never double-count coupons/principal. |
| Sale proceeds for Free | How to handle *unknown* exit fees, spread, liquidation discount or taxes; how to communicate low-confidence net proceeds. | Free uses net expected sale amount and must show caveats instead of treating unknown charges as zero. |
| Historical FX graph | Define explicit FX pair direction, permitted inverse/cross conversions and rounding of the displayed conversion. | Use latest observed rate **on/before evaluation date**, warn after 7 days, mark Total incomplete if no rate. |
| Contributions and control snapshots | Choose the day of the month on which a contribution is forecast, the precise cutoff for day-end snapshots, and the workflow for proposing/confirming reconciliation events. | Effective-dated monthly net contributions plus one-off events, observable monthly corrections, no silent mutation. |
| Payment settlement | How to treat overpayments, late payment on a day shared with another event, reversals and charges, and how to match several receipts to one scheduled obligation. | Plan versus fact stays separate; partial receipts are recorded individually; actual cash only on receipt. |
| Monthly/daily views | Define which date ranges enable the daily view, aggregation boundaries and how month-end snapshots display missing values. | Monthly points for the long (up to 50-year) forecast; daily detail for short periods. |
| Asset price freshness | Specific warning threshold per asset category, including manual property appraisals versus quoted securities. | Price and source dates stored; staleness depends on asset type; never use a single universal seven-day rule for every price. |
| Backup and recovery | Frequency, retention, target directory and atomic snapshot/restore procedure for SQLite, including integrity test. | Regular local backup, manual export and restore and verified recovery; no requirement for cloud upload. |
| Financial rounding | Currency-specific posting precision, instrument-specific fractional quantities and display precision, including error tolerance for exponential calculations. | Use Decimal; do not round arbitrary intermediates or equate 0 with unknown. |

## Future module: investment calculators and online data

A dedicated [Investment Calculators architecture](investment-calculators.md) reserves extensible engines for **OVGZ/other bonds, stocks/ETF, deposits, and other assets**. It starts with manual inputs and adds optional provider adapters, caching and read-only refresh while preserving offline work. NBU securities reference and FX are candidate official sources; stock/ETF quotes depend on provider licensing and coverage. Calculator what-if results never automatically become real transactions.

Before calculator implementation, specify coupon/accrued-interest conventions, bond YTM numeric method and day counts, fees/taxes per scenario, stock split/dividend handling, cash-flow IRR degeneracy, quote staleness thresholds, and provider credentials/rate limits. Resolve in the respective small PR, not as blockers to the core skeleton.

## Future module: bank statement analysis

The user has requested a planned **bank statement analysis capability** for identifying possibly unjustified spending and missed financial opportunities. The [Statement Analysis architecture](statement-analysis.md) defines a separate read-only import/normalization/deduplication/matching/analytics pipeline. The current MVP's manual snapshot and Q01–Q16 remain unchanged.

Before a future implementation PR, agree on the first import format/bank export, optional bank-account linkage, categories and personal budget thresholds, original-statement retention/encryption, analysis intervals, and which hypothetical opportunity scenarios are useful. These details are **not prerequisites for the MVP**. Local processing, evidence for every finding and user review are the default; no automatic financial operations or statement upload to AI services.

## Explicitly deferred features

- Official APIs for FX, market prices, inflation and securities data; manual entry and offline operation remain available when adapters arrive.
- Import of previous operations from CSV/XLSX; start MVP with a manual dated snapshot instead.
- Bank/broker synchronization, cloud sync and multi-user collaboration.
- Advanced portfolio scenarios, automatic reinvestment **only if later explicitly approved** (not allowed as default), and sophisticated tax automation.
- Early bond sales or early deposit closures beyond ordinary dated sell/withdrawal events may require dedicated rules for fees, accrued interest and closing dates.

## Guidance for Codex tasks

1. First link the task to the approved product rule in [mvp.md](mvp.md) and its record/algorithm in [domain-model.md](domain-model.md).
2. If an edge case above is needed for the PR, propose a **small decision with test examples**; do not invent it.
3. Provide tests for boundary dates, missing values, zero amounts, duplicate prevention and currency handling.
4. Keep all changes on a feature branch in a Draft PR. The user reviews and performs the merge manually.

The [Babki reference](reference/babki.md) is **non-normative** UI/workflow inspiration only.
