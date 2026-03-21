"""
mortgage_db.py — SQL Server database helpers for Canadian Mortgage Analyzer
FIX #5: If tables not found, run setup_db.sql automatically.
Python 3.8 compatible — uses Optional[] instead of X | None union syntax.
"""
import json
import os
from typing import Optional


def get_db_connection(server, database, trusted, user="", pwd=""):
    """Connect to SQL Server and initialize tables. Returns (conn, error_str)."""
    try:
        import pyodbc
        cs = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};DATABASE={database};"
        ) + ("Trusted_Connection=yes;" if trusted else f"UID={user};PWD={pwd};")
        conn = pyodbc.connect(cs, timeout=5)
        _init_db(conn)
        return conn, None
    except Exception as e:
        return None, str(e)


def _run_sql_file(conn, sql_path):
    """Execute a SQL file against an open connection."""
    try:
        with open(sql_path, "r") as f:
            sql_text = f.read()
        # Split on GO statements (T-SQL batch separator)
        batches = [b.strip() for b in sql_text.split("\nGO") if b.strip()]
        if not batches:
            batches = [sql_text]
        c = conn.cursor()
        for batch in batches:
            if batch:
                try:
                    c.execute(batch)
                except Exception:
                    pass  # Ignore individual statement errors
        conn.commit()
        return True
    except Exception:
        return False


def _tables_exist(conn):
    """Check if required tables already exist."""
    try:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM sysobjects "
            "WHERE name IN ('mortgage_setup','mortgage_scenarios') AND xtype='U'"
        )
        row = c.fetchone()
        return row and int(row[0]) >= 2
    except Exception:
        return False


def _init_db(conn):
    """Ensure required tables exist. Falls back to setup_db.sql if present.
    NEVER drops or truncates existing data — only creates tables if absent."""
    # FIX #5: try SQL file first (from script folder), then inline DDL
    if not _tables_exist(conn):
        sql_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_db.sql")
        if os.path.exists(sql_file):
            _run_sql_file(conn, sql_file)

    # Inline fallback DDL (always safe — guarded by IF NOT EXISTS, no DROP/TRUNCATE)
    c = conn.cursor()
    c.execute(
        "IF NOT EXISTS(SELECT*FROM sysobjects WHERE name='mortgage_setup' AND xtype='U') "
        "CREATE TABLE mortgage_setup("
        "id INT IDENTITY PRIMARY KEY, "
        "saved_at DATETIME DEFAULT GETDATE(), "
        "setup_data NVARCHAR(MAX))"
    )
    c.execute(
        "IF NOT EXISTS(SELECT*FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U') "
        "CREATE TABLE mortgage_scenarios("
        "id INT IDENTITY PRIMARY KEY, "
        "name NVARCHAR(200), "
        "created_at DATETIME DEFAULT GETDATE(), "
        "params NVARCHAR(MAX), "
        "summary NVARCHAR(MAX))"
    )
    conn.commit()


# ── Setup CRUD ────────────────────────────────────────────────────────────────

def db_load_setup(conn):
    # type: (...) -> Optional[dict]
    if not conn:
        return None
    try:
        c = conn.cursor()
        c.execute("SELECT TOP 1 setup_data FROM mortgage_setup ORDER BY id DESC")
        r = c.fetchone()
        return json.loads(r[0]) if r else None
    except Exception:
        return None


def db_save_setup(conn, data):
    # type: (object, dict) -> bool
    if not conn:
        return False
    try:
        c = conn.cursor()
        c.execute("DELETE FROM mortgage_setup")
        c.execute(
            "INSERT INTO mortgage_setup(setup_data) VALUES(?)",
            json.dumps(data, default=str)
        )
        conn.commit()
        return True
    except Exception:
        return False


# ── Scenario CRUD ─────────────────────────────────────────────────────────────

def db_save_scenario(conn, name, params, summary):
    # type: (object, str, dict, dict) -> bool
    """Insert a new scenario row. Returns True on success."""
    if not conn:
        return False
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO mortgage_scenarios(name,params,summary) VALUES(?,?,?)",
            name,
            json.dumps(params, default=str),
            json.dumps(summary, default=str),
        )
        conn.commit()
        return True
    except Exception:
        return False


def db_update_scenario(conn, sid, name, params, summary):
    # type: (object, int, str, dict, dict) -> bool
    if not conn:
        return False
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE mortgage_scenarios SET name=?,params=?,summary=? WHERE id=?",
            name,
            json.dumps(params, default=str),
            json.dumps(summary, default=str),
            sid,
        )
        conn.commit()
        return True
    except Exception:
        return False


def db_load_scenarios(conn):
    # type: (...) -> list
    """Return list of scenario dicts ordered newest first."""
    if not conn:
        return []
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id,name,created_at,params,summary "
            "FROM mortgage_scenarios ORDER BY created_at DESC"
        )
        rows = c.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "created_at": str(r[2]),
                "params": json.loads(r[3]),
                "summary": json.loads(r[4]),
            }
            for r in rows
        ]
    except Exception:
        return []


def db_delete_scenario(conn, sid):
    # type: (object, int) -> None
    if not conn:
        return
    try:
        c = conn.cursor()
        c.execute("DELETE FROM mortgage_scenarios WHERE id=?", sid)
        conn.commit()
    except Exception:
        pass
