# Fin MVP open questions

The MVP can proceed with the documented financial rules. The questions below cover additional behavior to consider after the scheduled cash projection is in use. [MVP specification](mvp.md) · [Domain model](domain-model.md)

| Area | Product decision |
| --- | --- |
| Payment reconciliation | Should users be able to confirm an actual payment amount and settlement date? Decide how delayed, partial, and missed payments would affect available cash and whether Fin should keep the original schedule alongside actual receipts. |
| Changes to a holding | Which unscheduled events should Fin support, such as selling a bond early, withdrawing a deposit with a penalty, or renewing a deposit? Decide what event determines when the original holding closes and what proceeds are available for reuse. |
| Withholding and settlement charges | Should Fin record taxes, accrued interest at purchase, or settlement charges as separate amounts? Decide which charges affect acquisition cost, which reduce a payment, and where users should see gross and net amounts. |
| Investment yield | Which calculated yield, if any, should appear alongside the optional target annual yield? If Fin calculates a yield from dated contributions and receipts, decide which fees and cashflows enter the calculation and how to handle positions whose cashflows do not produce a unique yield. |
