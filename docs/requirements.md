# Fin — Requirements

## Goal
A single-user personal financial planning application with a monthly timeline for up to 50 years: available cash, invested capital, assets, liabilities, nominal net worth, and inflation-adjusted net worth.

## Operating constraints
- Web interface for Windows and Linux browsers.
- Fully offline operation, with no mandatory cloud services.
- Development on Windows with WSL; deployment on Linux.
- Remote access only through an owner-controlled VPN.
- Support multiple currencies with explicit conversion assumptions.

## Financial data
- Cash and bank accounts.
- Bank deposits with distinct maturity dates and interest payouts.
- Ukrainian government bonds (OVGZ), including coupons and principal redemption.
- Extensible holdings: shares, ETFs, cryptocurrency, real estate, and other assets.
- Loans, debts, recurring expenses, and future obligations.
- Multiple independent holdings and payment schedules.

## Calculations and views
- Monthly snapshots without mandatory transaction-by-transaction bookkeeping.
- Forecast maturities, coupons, interest, redemptions and available cash.
- Nominal and inflation-adjusted values with a stated base date.
- Configurable inflation, FX and return scenarios, including comparison views.
- Automated financial tests and traceable assumptions.

## Open questions
Reporting currency; FX sources; taxes and fees; reinvestment; snapshot entry/import; valuation of illiquid assets; authentication; backup and recovery.

## Not part of initial MVP
Automated bank/broker integrations, trading automation, multi-user SaaS and a full transaction ledger.
