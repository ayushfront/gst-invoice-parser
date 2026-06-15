import logging
import os
import sqlite3
import time
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

# Gemini 2.0-flash pricing (per token)
COST_PER_INPUT_TOKEN = 0.0000001     # $0.10 per million input tokens
COST_PER_OUTPUT_TOKEN = 0.0000004    # $0.40 per million output tokens
COMPUTE_COST_PER_CALL = 0.001        # Railway estimated compute cost

REVENUE_PER_CALL = float(os.environ.get("REVENUE_PER_CALL_USD", "0.08"))

# Circuit breaker: halt if a single call's loss exceeds this
CIRCUIT_BREAKER_DAILY_LOSS_LIMIT = -1.00
MIN_PROFIT_MARGIN_PER_CALL = -0.01   # allow tiny loss, never large


def _db_path() -> str:
    path = os.environ.get("SQLITE_DB_PATH", "./data/tracker.db")
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    return path


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            success INTEGER NOT NULL,
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
    """)
    conn.commit()


def _retry(fn, retries: int = 3, delay: float = 0.1):
    """Retry fn on sqlite3.OperationalError (e.g. DB locked)."""
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.error("SQLite still locked after %d retries: %s", retries, exc)
                return None


def log_call(
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    file_type: str = "unknown",
    error_code: str = None,
) -> dict | None:
    """
    Calculate cost/profit and write to call_log. Returns the row dict.
    Raises RuntimeError if circuit breaker triggers.
    """
    token_cost = (
        input_tokens * COST_PER_INPUT_TOKEN +
        output_tokens * COST_PER_OUTPUT_TOKEN
    )
    estimated_cost = token_cost + COMPUTE_COST_PER_CALL
    revenue = REVENUE_PER_CALL if success else 0.0
    profit = revenue - estimated_cost

    if profit < MIN_PROFIT_MARGIN_PER_CALL:
        _trigger_circuit_breaker(estimated_cost, revenue)
        raise RuntimeError(
            "INTERNAL_ERROR|Service temporarily halted due to cost anomaly|"
        )

    def _write():
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO call_log
                    (timestamp, success, input_tokens, output_tokens,
                     estimated_cost_usd, revenue_usd, profit_usd, file_type, error_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    int(success),
                    input_tokens,
                    output_tokens,
                    estimated_cost,
                    revenue,
                    profit,
                    file_type,
                    error_code,
                ),
            )
            conn.commit()
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return {
                "id": row_id,
                "estimated_cost_usd": estimated_cost,
                "revenue_usd": revenue,
                "profit_usd": profit,
            }
        finally:
            conn.close()

    return _retry(_write)


def get_daily_summary(for_date: date | None = None) -> dict:
    d = (for_date or date.today()).isoformat()

    def _read():
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*), SUM(success), SUM(estimated_cost_usd),
                       SUM(revenue_usd), SUM(profit_usd)
                FROM call_log WHERE DATE(timestamp) = ?
                """,
                (d,),
            ).fetchone()
            return {
                "date": d,
                "total_calls": row[0] or 0,
                "successful_calls": int(row[1] or 0),
                "total_cost_usd": round(row[2] or 0, 6),
                "total_revenue_usd": round(row[3] or 0, 4),
                "total_profit_usd": round(row[4] or 0, 4),
            }
        finally:
            conn.close()

    result = _retry(_read)
    return result or {"date": d, "total_calls": 0, "successful_calls": 0,
                      "total_cost_usd": 0, "total_revenue_usd": 0, "total_profit_usd": 0}


def _trigger_circuit_breaker(cost: float, revenue: float) -> None:
    logger.critical(
        "CIRCUIT BREAKER: cost $%.6f exceeds revenue $%.4f. "
        "Service will halt for this call.",
        cost, revenue,
    )
