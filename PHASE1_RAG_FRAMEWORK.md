# Phase 1 — RAG Framework Definition

## Objective
Define a deterministic, repeatable method to assign every project a single **RAG status** (Red / Amber / Green) from measurable signals.

---

## Signals & Weights

| Signal | Weight | Description |
|--------|--------|-------------|
| **Schedule** | 0.35 | Normalized days late vs. project duration. |
| **Budget** | 0.25 | Variance = (% spent − % complete). Positive = over budget. |
| **Milestones** | 0.15 | % overdue + % at-risk. |
| **Blockers** | 0.15 | Count × severity (critical=4, high=3, medium=2, low=1). |
| **Sentiment** | 0.10 | Avg stakeholder score (negative=2, neutral=1, positive=0). |

Total weight = 1.0. Each signal produces a **0–2 sub-score** (0=Green, 1=Amber, 2=Red). Weighted sum → composite 0–2.

---

## Thresholds

| Composite | RAG | Meaning |
|-----------|-----|---------|
| 0.00 – 0.50 | 🟢 **Green** | On track; minor variance tolerated. |
| 0.51 – 1.25 | 🟠 **Amber** | Attention needed; one or more signals degraded. |
| 1.26 – 2.00 | 🔴 **Red** | Escalation required; multiple or severe issues. |

---

## Signal Calculations

### Schedule (0–2)
```
raw_late = max(0, (today − end_date).days)          # if project ended
         or max(0, (today − next_milestone_due).days)  # else next milestone
pct_late = raw_late / max(1, (end_date − start_date).days)

if pct_late == 0:                score = 0
elif pct_late <= 0.10:           score = 1
else:                            score = 2
```
Hard cap: `raw_late ≤ 730` days (2 years) to prevent outliers.

### Budget (0–2)
```
variance = (spent / budget) − (complete_pct / 100)   # both as fractions
if variance <= 0.05:       score = 0
elif variance <= 0.20:     score = 1
else:                      score = 2
```
Narrative phrasing: "X pt over/under budget" (directional, not raw dollars).

### Milestones (0–2)
```
overdue_frac = overdue_count / total_milestones
atrisk_frac  = at_risk_count  / total_milestones
combined = overdue_frac + 0.5 * atrisk_frac

if combined == 0:       score = 0
elif combined <= 0.25:  score = 1
else:                   score = 2
```

### Blockers (0–2)
```
severity_weight = Σ blocker_severity   # critical=4, high=3, med=2, low=1
if severity_weight == 0:        score = 0
elif severity_weight <= 4:        score = 1
else:                             score = 2
```

### Sentiment (0–2)
```
avg_score = mean(stakeholder_sentiment_scores)  # negative=2, neutral=1, positive=0
if avg_score == 0:      score = 0
elif avg_score <= 1.0:  score = 1
else:                   score = 2
```
If no sentiment entries → excluded from composite (weight redistributed).

---

## Override Rules (Red regardless of composite)

1. **Critical blocker** open > 5 business days.
2. **Zero milestones** defined for active project.
3. **No snapshot** in last 30 days (data staleness).
4. **End date < start date** (data integrity).

---

## Implementation Notes

- Computed in `src/models/rag.py:compute_rag()`.
- Weekly scheduled job (`schedule.py:_run_weekly`) writes RAG + narrative.
- API endpoint `/api/projects/{id}/rag` returns live calculation.
- LLM narrative (`reports.py:weekly_narrative`) consumes the same `RagResult` for consistency.

---

## Future Extensibility

- Add **risk register** signal (identified risks × probability × impact).
- Add **resource allocation** signal (planned vs. actual FTEs).
- Make weights configurable per portfolio via DB table.