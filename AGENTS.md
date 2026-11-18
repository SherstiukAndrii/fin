# Codex instructions for Fin

Read docs/requirements.md, docs/architecture.md and docs/roadmap.md before implementation.

Propose a plan and explain architectural decisions before modifying code. Make small reviewable changes, add tests for financial calculations, and show diffs. Do not commit or push without explicit permission.

Use precise decimal arithmetic for money, explicit currencies and rounding rules. Separate actual data from projections. Keep the application offline-first and single-user, with VPN-only remote access.
