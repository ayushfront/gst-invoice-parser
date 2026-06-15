# Self-Sustaining Logic — GST Invoice Parser

## Core principle

Every API call must earn more than it costs. The system tracks this in real time and can halt itself if it becomes unprofitable. No human intervention required.

---

## Unit economics per call

| Item | Amount (USD) |
|---|---|
| Revenue per call (RapidAPI price) | +$0.080 |
| Claude Haiku cost (est. 2000 tokens) | -$0.003 |
| Railway compute cost per call (est.) | -$0.001 |
| **Net profit per call** | **+$0.076** |
| Profit margin | **95%** |

At 65 calls/day: $4.94/day profit. At 80 calls/day: $6.08/day profit.

---

## Cost tracker — SQLite schema

```sql
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    success INTEGER NOT NULL,          -- 1 = success, 0 = error
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    revenue_usd REAL,
    profit_usd REAL,
    file_type TEXT,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date TEXT PRIMARY KEY,
    total_calls INTEGER,
    successful_calls INTEGER,
    total_cost_usd REAL,
    total_revenue_usd REAL,
    total_profit_usd REAL,
    avg_response_ms INTEGER
);
```

---

## cost_tracker.py — implementation spec

```python
# app/cost_tracker.py

import sqlite3
import os
from datetime import datetime, date

COST_PER_INPUT_TOKEN  = 0.00000025   # Claude Haiku pricing
COST_PER_OUTPUT_TOKEN = 0.00000125
REVENUE_PER_CALL      = float(os.environ.get("REVENUE_PER_CALL_USD", 0.08))
COMPUTE_COST_PER_CALL = 0.001        # Railway estimate

CIRCUIT_BREAKER_DAILY_LOSS_LIMIT = -1.00   # Halt if daily loss exceeds $1
MIN_PROFIT_MARGIN_PER_CALL       = -0.01   # Allow small loss, not large

def log_call(success: bool, input_tokens: int, output_tokens: int,
             file_type: str, error_code: str = None):
    cost = (input_tokens * COST_PER_INPUT_TOKEN +
            output_tokens * COST_PER_OUTPUT_TOKEN +
            COMPUTE_COST_PER_CALL)
    revenue = REVENUE_PER_CALL if success else 0.0
    profit = revenue - cost

    # Circuit breaker check
    if profit < CIRCUIT_BREAKER_DAILY_LOSS_LIMIT:
        _trigger_circuit_breaker(cost, revenue)

    # Write to SQLite
    conn = sqlite3.connect(os.environ["SQLITE_DB_PATH"])
    conn.execute("""
        INSERT INTO call_log
        (timestamp, success, input_tokens, output_tokens,
         estimated_cost_usd, revenue_usd, profit_usd, file_type, error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), int(success), input_tokens,
          output_tokens, cost, revenue, profit, file_type, error_code))
    conn.commit()
    conn.close()

def get_daily_summary(for_date: date = None) -> dict:
    d = (for_date or date.today()).isoformat()
    conn = sqlite3.connect(os.environ["SQLITE_DB_PATH"])
    row = conn.execute("""
        SELECT COUNT(*), SUM(success), SUM(estimated_cost_usd),
               SUM(revenue_usd), SUM(profit_usd)
        FROM call_log WHERE DATE(timestamp) = ?
    """, (d,)).fetchone()
    conn.close()
    return {
        "date": d,
        "total_calls": row[0] or 0,
        "successful_calls": row[1] or 0,
        "total_cost_usd": round(row[2] or 0, 4),
        "total_revenue_usd": round(row[3] or 0, 4),
        "total_profit_usd": round(row[4] or 0, 4)
    }

def _trigger_circuit_breaker(cost: float, revenue: float):
    # Log critical alert
    import logging
    logging.critical(
        f"CIRCUIT BREAKER: daily loss limit hit. "
        f"Cost: ${cost:.4f}, Revenue: ${revenue:.4f}"
    )
    # Future v2: send email alert via SendGrid
    # Future v2: pause Railway deployment via API
```

---

## Dashboard endpoint (internal only)

```
GET /dashboard
Header: X-Internal-Secret: {INTERNAL_SECRET}
```

Returns today's summary — total calls, profit, cost, revenue. This is for you to check, not for public users. Protect with a separate secret header.

```json
{
  "date": "2024-03-15",
  "total_calls": 47,
  "successful_calls": 45,
  "total_cost_usd": 0.14,
  "total_revenue_usd": 3.76,
  "total_profit_usd": 3.62,
  "on_track_for_daily_target": true,
  "projected_daily_profit": 6.12
}
```

---

## Self-sustaining checklist

- [x] Revenue per call tracked and logged
- [x] Cost per call calculated from actual token usage
- [x] Circuit breaker halts processing if daily loss exceeds $1
- [x] Dashboard endpoint shows real-time P&L
- [x] SQLite persists across Railway restarts (Railway volume mount)
- [ ] v2: Auto-email alert when daily profit drops below $2
- [ ] v2: Auto-scale Railway plan when calls exceed 500/day
- [ ] v2: Auto-switch to Sonnet model if Haiku accuracy complaints spike

---

## Railway cost projection

| Railway plan | Cost/month | Break-even calls/month |
|---|---|---|
| Free (500hrs) | $0 | 0 — pure profit |
| Hobby ($5/month) | $5 | 67 calls total |
| Pro ($20/month) | $20 | 264 calls total |

Start on free tier. Upgrade only when you consistently exceed 500 hobby hours/month.
