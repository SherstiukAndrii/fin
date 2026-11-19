# Babki workbook mapping

The supplied `Babki.xlsx` provides the formula and cell references for `Bank` and `Overview`. The supplied `Babki - Overview.csv` provides a second view of displayed values and daily row layout. It has six columns, 1,008 rows, and zero formula expressions. This document cites workbook cells and formula text, with no copied balances or holdings.

The cloud proxy returned HTTP 403 while connecting to both the live spreadsheet page and the online XLSX export endpoint. The mapping describes the attached export. The live sheet’s recalculation and locale behavior remain unverified.

## Overview workbook formulas

### Summary

Labels in rows 1–7 include `Нал` (cash), `Bank`, `Курс` (FX rates), `karta`, `Total`, `Freezed`, `Returned`, `Inf`, `Dep`, `В месяц +` (monthly contribution), and `Инфляция` (inflation).

| Workbook cells | Observed calculation or input | Application concept |
| --- | --- | --- |
| `B1:D2` | Cash and bank amounts in UAH, USD, and EUR, in that order. `B1` is blank in this export. | Opening balances grouped by kind and currency |
| `C3:D3` | Entered conversion rates. The formulas multiply each foreign balance by its corresponding rate. | Manual UAH per USD and UAH per EUR |
| `B4:C4` | `B4 = SUM(B1:B2)+IF(C4,SUM(C1:C2)*$C$3+SUM(D1:D2)*$D$3,0)`. `C4` is a Boolean input. | Starting liquidity with optional foreign-currency inclusion |
| `F1` | Sum `Bank!H9:H1000` where the purchase date in `Bank!B9:B1000` is on or before `TODAY()` and the maturity returned by an exact lookup on `Bank!A2:B7` is after `TODAY()`. The formula suppresses row errors and uses zero for a failed lookup. | Active acquisition cost as of the workbook's current date (`Freezed`) |
| `F2` | `Returned` uses a Google Sheets expression exported in an Excel compatibility wrapper. Its expression converts `Bank!F8` to text, strips characters outside digits, period and minus, and applies `ABS`. The outer `IFERROR` contains a workbook-specific numeric fallback. | Returned amount derived from Bank. Confirm the offline rule and scope before implementing. |
| `B5` | `SUM(B1:B2)+SUM(C1:C2)*$C$3+SUM(D1:D2)*$D$3+F1`. It always includes converted foreign balances. | `Total` at active acquisition cost plus cash and bank balances |
| `B6:B7` | Entered monthly contribution and annual inflation rate. | Projection inputs |
| `D5` | `$B$5*$B$7*(1/365)`. | Daily simple inflation estimate (`Inf` summary) |
| `F5` | `$F$1*+Bank!$E$8*(1/365)`. | Daily simple yield estimate (`Dep` summary) |

### Daily projection

In the formulas below, `d0` is the entered start date from `A9`, `d` is the current projection date, `previous` is the row one calendar day earlier, and `y` is `Bank!E8`. The summary and daily formulas share inputs, but `TODAY()` in `F1` is independent of the start date in `A9`.

| Workbook cells | Observed calculation | Application concept |
| --- | --- | --- |
| `A9:A1007` | `A9` is entered. Each later date is the previous date plus one day. There are 999 dated rows. | Projection date grid |
| `B9` | `$B$4`. | Initial `Free` |
| `B10:B1008` | Previous `Free` plus `Bank!F2:F7` proceeds matched by maturity date in `Bank!B2:B7`, minus `Bank!H9:H1518` purchase costs matched by purchase date in `Bank!B9:B1518`, plus `$B$6` when `DAY(d)=1`. `B1008` uses the same recurrence with a blank date. | Daily liquidity after maturity proceeds, purchases, and monthly contributions |
| `C9` | Zero. | Initial `Fire` |
| `C10:C1002` | Previous `Fire` plus previous `Free` times `((1+y)^((d-previousDate)/365)-1)`. | Accumulated modeled return on previous-day liquidity |
| `C1003:C1007` | Current `Free * (1+y)^((d-d0)/365)`. This switches to a different basis and includes principal. | Alternate `Fire` calculation pending approval |
| `D9:D1007` | `Total / (1+annualInflation)^((d-d0)/365)`. The numerator is the same summary `Total` on every date. | `Apr`, purchasing power of the starting total |
| `E9:E1007` | `Total - Apr`. | `Inf`, cumulative erosion of purchasing power |

The first line chart uses `A9:A1008` for categories and `B9:D1008` for the `Free`, `Fire`, and `Apr` series. The second uses `A9:A1007` and `D9:D1007` for `Apr` alone. The XLSX chart definitions omit an explicit blank-display setting. The `Inf` series appears in the table. Conditional rules over `A9:D1007` test for the first day of a month and for Mondays.

The workbook uses two different maximum purchase row bounds. Review the `Freezed` bound, the trailing undated `Free` calculation, the late `Fire` switch, and the export fallback in [open questions](open-questions.md) before treating any change as part of parity.

## Bank instrument catalog

The workbook defines `Table1` at `Bank!A1:F7`. The same instrument name can appear on multiple purchase rows. Maturity date and redemption amount describe the observed calculation roles of the catalog `Date` and `Price` fields.

| Workbook cells | Observed source or formula | Application concept |
| --- | --- | --- |
| `Bank!A2:A7` | `Name`. Entry referenced by `SUMIF` and exact-match `VLOOKUP`. The purchase name input validation on `A9:A16` uses `Table1[Name]`. | Instrument display name and link to purchase lots. Use an internal instrument ID in the proposed model. |
| `Bank!B2:B7` | `Date`. Numeric spreadsheet date displayed as day and abbreviated month. The lookup result supplies the end date in lot yield. | Instrument maturity date. Confirm whether the deposit date also represents contractual maturity. |
| `Bank!C2:C7` | `Price`. UAH amount returned by the lot payout lookup. | Contractual payout per unit used in the workbook. Confirm treatment of any interest or coupon cash flows included in this amount. |
| `Bank!D2:D7` | `Count`. The shared formula starts at `D2` with `=SUMIF($A$9:$A2008,A2,$D$9:$D2008)`. Its starting rows are fixed and its ending rows move from `2008` to `2013` as it fills down. | Sum of recorded purchase quantities by instrument name. Confirm when mature lots should leave this count. |
| `Bank!E2:E7` | `Target`. Percentage-formatted numeric input. In the observed lot `VLOOKUP` calls, the ranges include this column and the return indices select `Date` or `Price`. | Candidate target-yield field. Confirm its meaning and whether it belongs in the first MVP. |
| `Bank!F2:F7` | `Income`. Shared formula starts at `F2` with `=D2*C2`. | Aggregate face or redemption proceeds using the recorded count. The source label is `Income`. |

## Bank purchase lots

The filled purchase rows are `Bank!A9:H14`. Columns `A:D` supply the instrument name, purchase date, UAH unit purchase price, and quantity. Row `9` points to the deposit catalog entry in row `2`. Rows `10:14` look up only the catalog below that entry. Preserve these source differences in parity examples and confirm the general entry rules before implementing them.

| Workbook cells | Observed formula or input | Application concept |
| --- | --- | --- |
| `Bank!E9` | `=IFERROR((F9-H9)/H9*365/(VLOOKUP(A9,$A$2:$E$7,2,FALSE)-B9),0)` | Deposit lot yield uses payout minus acquisition cost relative to cost, annualized by `365 / days`. Workbook errors become zero. |
| `Bank!E10:E14` | Shared formula starts at `E10` with `=IFERROR((F10-H10)/H10*365/(VLOOKUP(A10,$A$3:$E$7,2,FALSE)-B10),0)`. | Same annualization with the catalog range excluding row `2`. This is a simple annualized return on the lot. |
| `Bank!F9` | `=IFERROR(VLOOKUP(A9,$A$2:$E$7,3,FALSE)*D9,0)` | Expected gross payout from catalog `Price` times lot quantity. Workbook errors become zero. |
| `Bank!F10:F14` | Shared formula starts at `F10` with `=IFERROR(VLOOKUP(A10,$A$3:$E$7,3,FALSE)*D10,0)`. | Payout lookup excluding row `2`. |
| `Bank!G9`, `G10:G14` | `G9` holds a numeric entry. The shared formula for `G10:G14` starts with `=D10*C10*0.002`. | Entered deposit fee and a computed 0.2% fee on the other recorded purchases. Confirm fee rules for new lots and whether overrides are needed. |
| `Bank!H9:H14` | Shared formula starts at `H9` with `=D9*C9+G9`. | Lot acquisition cost including fee. The summary in `H8` adds the minus sign for display. |

## Bank summary row

| Workbook cell | Observed formula | Application concept |
| --- | --- | --- |
| `Bank!D8` | `="+ в месяц "&TEXT(ABS(VALUE(SUBSTITUTE(H8," ","")))*E8/12+B8,"# ##0")` | Monthly income display. It strips spaces and parses the formatted `H8`, takes its absolute value, applies `E8 / 12`, and adds `B8`, which is empty in the export. Confirm the intent of the adjustment and the effect of formatting `H8` before parsing it. |
| `Bank!E8` | `=IFERROR(SUMPRODUCT(E9:E2008,F9:F2008)/SUM(F9:F2008),0)` | Payout-weighted mean of lot yields. Formula errors, including division by zero, produce zero. `Overview` reads this cell. |
| `Bank!F8` | `="+"&TEXT(SUM(F9:F2008),"# ##0")` | Gross expected payout total formatted as text with a leading plus. `Overview!F2` reads this cell. |
| `Bank!G8` | `="👮"&TEXT(SUM(G10:G2008),"# ##0")` | Fee total formatted as text. The sum starts at row `10` and excludes `G9`, even though the lot cost in `H9` includes it. |
| `Bank!H8` | `="-"&TEXT(SUM(H9:H2008),"# ##0")` | Acquisition cost total formatted as text with a leading minus. `D8` consumes the formatted string. |

The proposed app can represent payout, fees, cost, and yield as numeric fields and apply formatting in the interface. Compare that representation against the workbook where a formula consumes formatted text, since preserving more precision can change the output. See the [proposed domain model](domain-model.md).

## Formula reconciliation

Use the XLSX formulas and chart ranges as the reference. To verify future behavior, compare approved synthetic examples against spreadsheet outputs before claiming parity. Cover deposit and bond lots, several lots for one instrument, fee aggregation, zero payout or duration, maturity-day movement, the first and last daily rows, and chart handling of empty values. Track changes to workbook formulas and unresolved semantics in [open questions](open-questions.md).
