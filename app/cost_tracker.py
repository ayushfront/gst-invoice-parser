import logging
import os
import sqlite3
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SQLITE_DB_PATH", "./data/tracker.db")
COST_PER_CALL_USD = float(os.getenv("COST_PER_CALL_USD", "0.003"))
REVENUE_PER_CALL_USD = float(os.getenv("REVENUE_PER_CALL_USD", "0.08"))
CIRCUIT_BREAKER_THRESHOLD = 0.0  # if running margin goes below $0 halt processing


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            success INTEGER NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            revenue_usd REAL,
            profit_usd REAL
        )
        """
    )
    conn.commit()


@contextmanager
def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        _ensure_db(conn)
        yield conn
    finally:
        conn.close()


def _retry_db(fn, retries: int = 3, delay: float = 0.1):
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.error("SQLite locked after %d retries: %s", retries, e)
                return None


def log_call(
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float | None = None,
) -> dict | None:
    if estimated_cost_usd is None:
        estimated_cost_usd = COST_PER_CALL_USD
    revenue = REVENUE_PER_CALL_USD if success else 0.0
    profit = revenue - estimated_cost_usd

    # Circuit breaker: if running at a loss, log critical and block
    if profit < CIRCUIT_BREAKER_THRESHOLD and success:
        logger.critical(
            "CIRCUIT BREAKER: cost per call ($%.4f) exceeds revenue ($%.4f). "
            "Halting and alerting.",
            estimated_cost_usd,
            revenue,
        )
        # Re-raise so the caller can decide to abort
        raise RuntimeError("INTERNAL_ERROR|Service temporarily halted due to cost anomaly|")

    def _write():
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO call_log
                    (timestamp, success, input_tokens, output_tokens,
                     estimated_cost_usd, revenue_usd, profit_usd)
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)
                """,
                (int(success), input_tokens, output_tokens, estimated_cost_usd, revenue, profit),
            )
            conn.commit()
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return {
                "id": row_id,
                "estimated_cost_usd": estimated_cost_usd,
                "revenue_usd": revenue,
                "profit_usd": profit,
            }

    return _retry_db(_write)


def get_last_log() -> dict | None:
    def _read():
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT id, estimated_cost_usd, revenue_usd, profit_usd FROM call_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                return {
                    "id": row[0],
                    "estimated_cost_usd": row[1],
                    "revenue_usd": row[2],
                    "profit_usd": row[3],
                }
            return None

    return _retry_db(_read)
