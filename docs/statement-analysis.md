# Future module: Statement Analysis (bank-account statement intelligence)

**Status:** approved architectural extension, **not part of the current Bank + Overview MVP**. The user wants to analyze bank statements to find potentially unjustified spending and missed financial opportunities. This page designs extension points; it does not approve specific heuristics, bank connections, automated financial decisions or external AI uploads.

[Fin MVP](mvp.md) · [Domain model](domain-model.md) · [Implementation details](open-questions.md)

## Product goal and boundaries

The module helps a single user understand spending and cash-management choices across multiple bank accounts. It creates **reviewable findings** rather than making moral judgments: an unusual expense may be perfectly intentional, and an apparent investment opportunity may be unsuitable because of currency risk, liquidity needs, taxes or safety.

Start with **read-only, user-initiated imports** of statements. Initially plan a simple CSV adapter, then optional XLSX, bank-specific CSV, OFX, CAMT/ISO 20022, MT940 and PDFs if support proves reliable. No direct bank authentication, scraping, payment initiation, transfers, automatic brokerage trades, mandatory cloud or third-party AI in the initial module. Do not change the already agreed manual opening snapshot for the MVP.

## Extension-friendly architecture

```text
Bank statement file / future bank adapter
                |
        Import adapter (format + bank)
                |
     Staging / validation / provenance
                |
      Normalize to BankTransaction
                |
     Deduplication + transfer matching
                |
   Optional reconciliation with ledger
                |
  Read-only analytical transaction view
                |
Categorization + recurring + anomaly rules
                |
 Opportunity estimates + evidence + review
                |
  User-reviewed findings / optional actions
```

Keep separate **ports / interfaces** in domain/services and adapters in infrastructure:

- `StatementSource`: enumerate and read user-selected statement data. An adapter knows a source format; domain logic does not know CSV columns, banks or PDF layout.
- `StatementNormalizer`: map imported rows to a source-independent, explicitly typed transaction representation. Validate dates, sign, currencies and opening/closing balances where provided.
- `StatementRepository`: persist import batch, immutable raw provenance reference, normalized rows and reconciliation links. Preserve original source semantics.
- `ReconciliationService`: identify duplicates, bank reversals, card refunds and transfers between owned accounts; match statement rows to known payments/events without creating a second financial entry.
- `ExpenseClassifier`, `RecurringDetector`, `AnomalyDetector`, `OpportunityAnalyzer`: independent, versioned rule engines running on normalized analytical views; rules and user preferences stay separate from account balances.
- `FindingRepository`: store why an issue was flagged, supporting transaction IDs, estimated cost/saving, uncertainty, action state and user feedback. No finding directly modifies a balance.

Design all interfaces so a Python implementation can later be replaced or accelerated by C++ calculation components without changing persisted financial meaning. A local analytical view can be materialized on demand without altering transaction source-of-truth.

## Suggested records (separate namespace; precise schema deferred)

| Record | Minimum relevant fields |
| --- | --- |
| `StatementImportBatch` | ID, account link, bank/parser version, source format and file hash, imported-at, statement period, row count, status, validation warnings; optional local encrypted original file |
| `BankTransaction` | ID, batch/row provenance, bank transaction/reference ID when available, account, booking date and value date, original description/payee, signed Decimal amount, native currency, original currency/amount and FX/fee if given, account balance after operation if available |
| `TransactionMatch` | Statement row IDs linked to own-account transfer pair, bank reversal/refund, known purchase, scheduled/actual bond coupon or deposit interest, loan movement or ledger event, with confidence and review status |
| `TransactionCategory` | Category code, optional user override, rule version, confidence and tags (expense, income, own transfer, investment principal, yield, refund, cash withdrawal, unknown) |
| `AnalysisRule` | Rule identifier, version, parameters, thresholds, account scope, currency and exclusions; optionally a local user-defined budget |
| `AnalysisFinding` | Category, severity/priority, evidence links, observed amount/currency/period, estimated possible savings or range, assumptions, confidence, status: new/confirmed/dismissed/ignored |
| `AnalysisFeedback` | User correction of category/merchant, false positive, excluded merchant or confirmed opportunity; timestamp and rule reference |

Do not treat a source file hash as the only duplicate key: the same underlying transaction may appear in overlapping statements, in different export formats, or with changed descriptions. Use bank identifiers where reliable, otherwise a transparent, reviewed compound fingerprint (account, date/value date, amount, currency, reference) and keep a record of the match.

## Analytical questions

**Potentially unjustified or unusual spending:**
- Duplicate/near-duplicate merchant charges, repeat reversals and accidentally renewed services; do not flag a refund plus original charge as two expenses.
- Recurring subscriptions, automatic renewals and unnoticed price increases; show dates, merchant, historical cadence and monthly/annual equivalent.
- Spending exceeding **user-defined** category limits, unusually large transactions, spikes versus a meaningful comparable period, increased fees or unfavorable FX spread. Flag low-confidence categorization.
- Multiple similar services or high-cost credit/overdraft fees. Cash withdrawals and transfers among owned accounts are **not spending by default**; outflows to investment accounts are transfers of assets, not consumption.

**Missed opportunities (scenario estimates, not guaranteed returns):**
- Repeated unnecessary banking fees or FX conversion charges, with an estimate of what changing account/product or payment method *might* save.
- Idle cash held above a **user-defined liquidity reserve** versus a hypothetical savings account, deposit or OVGZ yield; show currency mismatch, deposit lock-up, early-exit cost, taxes/fees, liquidity and rate uncertainty. No assumption that the user should invest funds needed for emergencies.
- Regular contributions, uninvested bond redemptions or recurring costs that make a hypothetical change in cash flow worth reviewing.
- Compare alternative scenarios **only using sourced/dated rates or explicit user-entered assumptions**. A missing rate means “estimate unavailable”, not fabricated income. Historical yields and the Fire metric must not be silently treated as guaranteed future yields.

## Calculation and safety invariants

1. **Analysis does not create transactions.** Imported rows stay in an analytical/staging layer. Matching or promotion into the primary ledger requires explicit user approval and is idempotent. Existing monthly snapshots and manual ledger events remain authoritative until separately reconciled.
2. **Count consumption, not every debit.** Treat internal transfers, securities purchases and loan principal as movements between balance-sheet categories, not day-to-day spending. Treat investment coupons as yield but do not double-add them when matched to recorded actual payments. Refunds/reversals are tracked explicitly; fees and interest remain distinct.
3. **No automatic external actions.** Findings may recommend reviewing a subscription, bank fee or rate but never cancel a service, open a deposit, move money or buy OVGZ without a separately authorized workflow.
4. **Evidence, confidence and uncertainty.** Each finding states its specific linked rows, time window, reason and savings formula. Allow correcting categorizations, dismissing false positives and setting exclusions. Distinguish *measured loss* from *hypothetical opportunity cost*. Avoid calling an expense “unreasonable” as a fact.
5. **Currency correctness.** Keep native amounts, transaction-time FX where supplied and reporting FX with observation dates. Do not aggregate different currencies without a known conversion, and do not infer card FX costs merely from incomplete source records.
6. **Security and privacy.** Local-only by default, no raw statements or account numbers in logs, Issues, PRs, CI fixtures or external AI. Mask account identifiers, enforce local permissions, encrypt stored originals if retained, define user-controlled deletion/retention, and keep originals out of backups unless deliberately included securely. Optional cloud LLM analysis requires separate explicit consent, redaction and provider review.

## Sample future acceptance tests (synthetic data)

- Import an overlapping CSV extract twice: no second expense or balance debit; source batch and deduplication decision remain inspectable.
- Transfer 500 UAH between two owned accounts: match two sides; **0 UAH expense** and **0 UAH net-worth change**, with a warning if the counterparty side is absent.
- Import a 100 UAH bond coupon matching a manually recorded actual coupon: identify a match, never show 200 UAH cash or double realized Fire income.
- Detect a monthly subscription rise from 120 to 150 UAH: show a **30 UAH/month** change and the linked payment history; allow “intentional” dismissal.
- A 1,000 UAH cash withdrawal remains “cash movement / needs categorization”, not automatically 1,000 UAH of consumption.
- A hypothetical “idle funds” finding shows period, balance, liquidity reserve, rate source/date, gross/net estimate and caveats; with no reliable rate it shows **no estimated opportunity**.

## Delivery sequence

1. **Architecture only now:** define separation, possible extension ports and safety invariants in docs; keep Bank + Overview MVP scope unchanged.
2. **Later PR — Statement Import:** one CSV adapter, staging/normalization/deduplication, local storage and privacy tests.
3. **Later PR — Reconciliation:** transfer/refund/payment matching, safe user review and ledger linkage.
4. **Later PR — Expense Analytics:** user-defined categories, recurring and duplicate charges, explainable anomaly findings.
5. **Later PR — Opportunity Analysis:** opt-in scenarios with explicit dated rates, net costs, liquidity/risk caveats and dismissable findings.
6. **Optional later integrations:** bank APIs and AI-assisted classification only after separate design and privacy decisions. All rules must work offline without AI.

The current Q01–Q16 decisions remain intact; this is a new, **future capability**, not a reversal of the no-transaction-detail MVP.
