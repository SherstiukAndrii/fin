# Fin — Architecture (draft)

## Principles
Offline-first, single-user modular monolith. Keep financial rules independent of web and storage layers. The stack choices below are proposals, not yet final architectural approvals.

## Proposed stack
- Python MVP, with isolated C++ calculation modules added later for learning.
- FastAPI backend.
- SQLite local database with versioned migrations.
- Lightweight server-rendered web interface and minimal JavaScript.
- pytest for deterministic tests.
- VS Code, Windows + WSL for development; Linux deployment.
- Owner-controlled VPN (such as WireGuard) for remote access.

## Modules
1. Domain: Money, Currency, Asset, Holding, Liability, Schedule.
2. Persistence: repositories, database schema, migrations and backups.
3. Projection engine: monthly timeline, deposit and bond payments, inflation, FX and scenarios.
4. Application services: snapshots, portfolio editing, projections and comparisons.
5. API/UI: local browser interface, validation and reports.
6. Operations: configuration, logging, backup/restore and VPN deployment.

Dependency direction: UI/API → services → domain. Persistence implements interfaces; domain should not depend on HTTP or SQLite.

## Conceptual entities
Account, Currency, Asset, Holding, CashflowSchedule, Liability, MonthlySnapshot, Valuation, FxRate, Scenario. This is a draft model, not an approved SQL schema. Bond schedules must support multiple coupon events and final principal redemption.

## Financial correctness
Use Decimal or integer minor units for money, never binary floating point. Keep currencies, rounding policies and FX dates explicit. Distinguish recorded values from projections and document inflation base dates.

## Security and deployment
No mandatory internet access; no secrets in Git. Run the service locally or on a VPN-only interface, not exposed publicly. Document and test backup restoration.

## Future C++ path
Port isolated, well-tested pure calculation components to modern C++ behind a defined interface. Compare outputs against Python reference tests before replacing implementations.
