# SPAM TEST SCORECARD

## Execution Summary
- **Total Bundles Fired**: 202
- **Categories Generated**:
  - Valid: 50
  - Malformed Schema: 20
  - Boundary/Edge: 24
  - Adversarial: 48
  - Byte-level Garbage: 60

## Outcomes by Category
- **a. Valid**: Accepted (or duplicate rejected): 50, Rejected: 0, Crash: 0, Hang: 0, Silent Drop: 0
- **b. Malformed Schema**: Accepted (or duplicate rejected): 6, Rejected: 14, Crash: 0, Hang: 0, Silent Drop: 0
- **c. Boundary/Edge**: Accepted (or duplicate rejected): 18, Rejected: 6, Crash: 0, Hang: 0, Silent Drop: 0
- **d. Adversarial**: Accepted (or duplicate rejected): 42, Rejected: 6, Crash: 0, Hang: 0, Silent Drop: 0
- **e. Byte-level Garbage**: Accepted (or duplicate rejected): 0, Rejected: 60, Crash: 0, Hang: 0, Silent Drop: 0

## Critical Findings
No critical findings (crashes, hangs, silent drops) occurred.

## Expectations (computed from this run)

| Expectation | Result | Observed |
| :--- | :--- | :--- |
| No crash, hang or silent drop in any category | PASS | crashes=0 hangs=0 silent drops=0 |
| Invalid bundles (malformed, edge, adversarial, garbage) are never accepted as new runs | PASS | b: 0, c: 0, d: 0, e: 0 |
| Every valid bundle is accepted (or rejected as a duplicate) | PASS | 50/50 |
| Same content, same project: first accepted, the rest rejected as duplicates | PASS | 5 bundles, 1 accepted, 4 rejected |
| Same content, different projects: all accepted | PASS | 5/5 accepted |

## Accepted as a NEW run, by category

| Category | Accepted as a new run |
| :--- | :--- |
| Valid | 46 of 50 |
| Malformed Schema | 0 of 20 |
| Boundary/Edge | 0 of 24 |
| Adversarial | 0 of 48 |
| Byte-level Garbage | 0 of 60 |

This scorecard reports what was measured. It deliberately assigns no quality scores.
