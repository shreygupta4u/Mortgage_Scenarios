"""mortgage_db.py — 3NF database helpers. Reads setup_db.sql on init."""
import os, json
from datetime import date


# ── Connection ────────────────────────────────────────────────────
def get_db_connection(server, database, trusted, user="", pwd=""):
    try:
        import pyodbc
        cs = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};"
              f"DATABASE={database};"
              + ("Trusted_Connection=yes;" if trusted else f"UID={user};PWD={pwd};"))
        conn = pyodbc.connect(cs, timeout=5)
        _init_db(conn)
        return conn, None
    except Exception as e:
        return None, str(e)


def _run_sql_file(conn, path):
    with open(path) as f:
        sql = f.read()
    c = conn.cursor()
    # Execute each GO-separated batch
    for batch in sql.split("\nGO"):
        batch = batch.strip()
        if batch and not batch.startswith("--"):
            try:
                c.execute(batch)
            except Exception:
                pass
    conn.commit()


def _init_db(conn):
    """Create 3NF tables. Reads setup_db.sql if present."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(this_dir), "setup_db.sql"),
        os.path.join(this_dir, "setup_db.sql"),
        os.path.join(os.getcwd(), "setup_db.sql"),
    ]
    for p in candidates:
        if os.path.exists(p):
            _run_sql_file(conn, p)
            return
    # Inline fallback (minimal)
    c = conn.cursor()
    for sql in [
        "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_setup' AND xtype='U') "
        "CREATE TABLE mortgage_setup(id INT IDENTITY PRIMARY KEY, purchase_price DECIMAL(15,2) NOT NULL, "
        "down_pct DECIMAL(5,2) NOT NULL DEFAULT 20, mortgage_type NVARCHAR(20) NOT NULL DEFAULT 'Fixed', "
        "pay_frequency NVARCHAR(30) NOT NULL DEFAULT 'Monthly', annual_rate DECIMAL(7,4) NOT NULL, "
        "amort_years INT NOT NULL DEFAULT 30, term_years DECIMAL(4,1) NOT NULL DEFAULT 3, "
        "start_date DATE NOT NULL, include_cmhc BIT NOT NULL DEFAULT 1, saved_at DATETIME DEFAULT GETDATE())",
        "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_past_renewals' AND xtype='U') "
        "CREATE TABLE mortgage_past_renewals(id INT IDENTITY PRIMARY KEY, setup_id INT, seq_num INT, "
        "start_date DATE, annual_rate DECIMAL(7,4), mortgage_type NVARCHAR(20), term_years DECIMAL(4,1))",
        "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_past_prepayments' AND xtype='U') "
        "CREATE TABLE mortgage_past_prepayments(id INT IDENTITY PRIMARY KEY, setup_id INT, seq_num INT, "
        "payment_date DATE, amount DECIMAL(15,2))",
        "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U') "
        "CREATE TABLE mortgage_scenarios(id INT IDENTITY PRIMARY KEY, name NVARCHAR(200), "
        "description NVARCHAR(2000) DEFAULT '', annual_lump DECIMAL(15,2) DEFAULT 0, "
        "lump_month INT DEFAULT 1, lump_start_year INT DEFAULT 1, lump_num_years INT DEFAULT 0, "
        "pay_increase_type NVARCHAR(20) DEFAULT 'None', pay_increase_val DECIMAL(10,2) DEFAULT 0, "
        "onetime_period INT DEFAULT 0, onetime_amount DECIMAL(15,2) DEFAULT 0, "
        "created_at DATETIME DEFAULT GETDATE(), updated_at DATETIME DEFAULT GETDATE())",
        "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenario_renewals' AND xtype='U') "
        "CREATE TABLE mortgage_scenario_renewals(id INT IDENTITY PRIMARY KEY, scenario_id INT, "
        "seq_num INT, mode NVARCHAR(20) DEFAULT 'By Date', effective_date DATE, effective_period INT DEFAULT 1, "
        "new_rate DECIMAL(7,4), mortgage_type NVARCHAR(20) DEFAULT 'Fixed', term_years DECIMAL(4,1) DEFAULT 3, "
        "actual_penalty DECIMAL(15,2) DEFAULT 0, misc_fees DECIMAL(15,2) DEFAULT 250, "
        "orig_posted_rate DECIMAL(7,4) DEFAULT 0, curr_posted_rate DECIMAL(7,4) DEFAULT 0)",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass
    conn.commit()


# ── Setup load/save ───────────────────────────────────────────────
def db_load_setup(conn):
    """Return setup dict with nested past_renewals and past_prepayments."""
    if not conn: return None
    try:
        c = conn.cursor()
        c.execute("SELECT TOP 1 id, purchase_price, down_pct, mortgage_type, pay_frequency, "
                  "annual_rate, amort_years, term_years, start_date, include_cmhc "
                  "FROM mortgage_setup ORDER BY id DESC")
        row = c.fetchone()
        if not row: return None
        sid, pp, dpct, mt, pf, ar, ay, ty, sd, ic = row

        c.execute("SELECT seq_num, start_date, annual_rate, mortgage_type, term_years "
                  "FROM mortgage_past_renewals WHERE setup_id=? ORDER BY seq_num", sid)
        past_renewals = [{"id": f"db_{r[0]}", "start_date_str": str(r[1]),
                          "rate": float(r[2]), "mtype": r[3], "term_years": float(r[4])}
                         for r in c.fetchall()]

        c.execute("SELECT seq_num, payment_date, amount "
                  "FROM mortgage_past_prepayments WHERE setup_id=? ORDER BY seq_num", sid)
        past_prepayments = [{"id": f"db_{r[0]}", "date_str": str(r[1]), "amount": float(r[2])}
                            for r in c.fetchall()]

        return {"widget_state": {"s_price": float(pp), "s_dpct": float(dpct),
                                 "s_mtype": mt, "s_freq": pf, "s_rate": float(ar),
                                 "s_amort": int(ay), "s_term": float(ty),
                                 "s_startdate": str(sd), "s_addcmhc": bool(ic)},
                "past_renewals": past_renewals, "past_prepayments": past_prepayments,
                "_setup_id": sid}
    except Exception:
        return None


def db_save_setup(conn, data):
    """Upsert: delete all rows, insert fresh normalized rows."""
    if not conn: return False
    try:
        c = conn.cursor()
        ws = data.get("widget_state", {})
        # Delete old data (cascade deletes renewals + prepayments)
        c.execute("DELETE FROM mortgage_past_prepayments WHERE setup_id IN (SELECT id FROM mortgage_setup)")
        c.execute("DELETE FROM mortgage_past_renewals WHERE setup_id IN (SELECT id FROM mortgage_setup)")
        c.execute("DELETE FROM mortgage_setup")

        sd_val = ws.get("s_startdate", "2023-08-15")
        sd_str = str(sd_val) if not isinstance(sd_val, str) else sd_val

        c.execute("INSERT INTO mortgage_setup "
                  "(purchase_price, down_pct, mortgage_type, pay_frequency, annual_rate, "
                  "amort_years, term_years, start_date, include_cmhc) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  float(ws.get("s_price", 1030000)), float(ws.get("s_dpct", 20)),
                  ws.get("s_mtype", "Fixed"), ws.get("s_freq", "Monthly"),
                  float(ws.get("s_rate", 5.39)), int(ws.get("s_amort", 30)),
                  float(ws.get("s_term", 3)), sd_str,
                  int(bool(ws.get("s_addcmhc", True))))
        c.execute("SELECT @@IDENTITY")
        setup_id = int(c.fetchone()[0])

        for i, rn in enumerate(data.get("past_renewals", []), 1):
            c.execute("INSERT INTO mortgage_past_renewals "
                      "(setup_id, seq_num, start_date, annual_rate, mortgage_type, term_years) "
                      "VALUES (?,?,?,?,?,?)",
                      setup_id, i, str(rn["start_date_str"]),
                      float(rn["rate"]), rn["mtype"], float(rn["term_years"]))

        for i, pp in enumerate(data.get("past_prepayments", []), 1):
            c.execute("INSERT INTO mortgage_past_prepayments "
                      "(setup_id, seq_num, payment_date, amount) VALUES (?,?,?,?)",
                      setup_id, i, str(pp["date_str"]), float(pp["amount"]))

        conn.commit()
        return True
    except Exception as e:
        print(f"db_save_setup error: {e}")
        return False


# ── Scenario load/save ────────────────────────────────────────────
def db_save_scenario(conn, sc_id_or_none, name, desc, renewals, pp_settings):
    """Insert or update a scenario with its renewals. Returns DB id."""
    if not conn: return None
    try:
        c = conn.cursor()
        pp = pp_settings  # dict with annual_lump, lump_month, etc.
        if sc_id_or_none:
            # Update
            c.execute("UPDATE mortgage_scenarios SET name=?, description=?, "
                      "annual_lump=?, lump_month=?, lump_start_year=?, lump_num_years=?, "
                      "pay_increase_type=?, pay_increase_val=?, "
                      "onetime_period=?, onetime_amount=?, updated_at=GETDATE() "
                      "WHERE id=?",
                      name, desc,
                      float(pp.get("annual_lump", 0)), int(pp.get("lump_month", 1)),
                      int(pp.get("lump_start_year", 1)), int(pp.get("lump_num_years", 0)),
                      pp.get("pay_increase_type", "None"), float(pp.get("pay_increase_val", 0)),
                      int(pp.get("onetime_period", 0)), float(pp.get("onetime_amount", 0)),
                      sc_id_or_none)
            db_id = sc_id_or_none
            c.execute("DELETE FROM mortgage_scenario_renewals WHERE scenario_id=?", db_id)
        else:
            # Insert
            c.execute("INSERT INTO mortgage_scenarios "
                      "(name, description, annual_lump, lump_month, lump_start_year, lump_num_years, "
                      "pay_increase_type, pay_increase_val, onetime_period, onetime_amount) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      name, desc,
                      float(pp.get("annual_lump", 0)), int(pp.get("lump_month", 1)),
                      int(pp.get("lump_start_year", 1)), int(pp.get("lump_num_years", 0)),
                      pp.get("pay_increase_type", "None"), float(pp.get("pay_increase_val", 0)),
                      int(pp.get("onetime_period", 0)), float(pp.get("onetime_amount", 0)))
            c.execute("SELECT @@IDENTITY")
            db_id = int(c.fetchone()[0])

        for i, rn in enumerate(renewals, 1):
            eff_date = rn.get("date_str", None)
            c.execute("INSERT INTO mortgage_scenario_renewals "
                      "(scenario_id, seq_num, mode, effective_date, effective_period, "
                      "new_rate, mortgage_type, term_years, actual_penalty, misc_fees, "
                      "orig_posted_rate, curr_posted_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      db_id, i, rn.get("mode", "By Date"),
                      str(eff_date) if eff_date else None, int(rn.get("period", 1)),
                      float(rn["new_rate"]), rn.get("mtype", "Fixed"), float(rn.get("term_years", 3)),
                      float(rn.get("actual_penalty", 0)), float(rn.get("misc_fees", 250)),
                      float(rn.get("orig_posted", 0)), float(rn.get("curr_posted", 0)))
        conn.commit()
        return db_id
    except Exception as e:
        print(f"db_save_scenario error: {e}")
        return None


def db_load_scenarios(conn):
    """Return list of scenario dicts with nested renewals."""
    if not conn: return []
    try:
        c = conn.cursor()
        c.execute("SELECT id, name, description, annual_lump, lump_month, lump_start_year, "
                  "lump_num_years, pay_increase_type, pay_increase_val, "
                  "onetime_period, onetime_amount, created_at "
                  "FROM mortgage_scenarios ORDER BY id")
        rows = c.fetchall()
        scenarios = []
        for row in rows:
            (sid, name, desc, al, lm, lsy, lny, pit, piv, otp, ota, cat) = row
            c2 = conn.cursor()
            c2.execute("SELECT seq_num, mode, effective_date, effective_period, new_rate, "
                       "mortgage_type, term_years, actual_penalty, misc_fees, "
                       "orig_posted_rate, curr_posted_rate "
                       "FROM mortgage_scenario_renewals WHERE scenario_id=? ORDER BY seq_num", sid)
            renewals = []
            import uuid as _uuid
            for r in c2.fetchall():
                (sn, mode, edate, eper, nr, mt, ty, ap, mf, op, cp) = r
                renewals.append({
                    "id": str(_uuid.uuid4())[:8],
                    "mode": mode or "By Date",
                    "date_str": str(edate) if edate else None,
                    "period": int(eper) if eper else 1,
                    "new_rate": float(nr),
                    "mtype": mt or "Fixed",
                    "term_years": float(ty) if ty else 3,
                    "actual_penalty": float(ap) if ap else 0,
                    "misc_fees": float(mf) if mf else 250,
                    "orig_posted": float(op) if op else 0,
                    "curr_posted": float(cp) if cp else 0,
                    "variable_subs": {},
                })
            pp_settings = {
                "annual_lump": float(al) if al else 0,
                "lump_month": int(lm) if lm else 1,
                "lump_start_year": int(lsy) if lsy else 1,
                "lump_num_years": int(lny) if lny else 0,
                "pay_increase_type": pit or "None",
                "pay_increase_val": float(piv) if piv else 0,
                "onetime_period": int(otp) if otp else 0,
                "onetime_amount": float(ota) if ota else 0,
            }
            scenarios.append({
                "db_id": sid, "name": name, "desc": desc or "",
                "renewals": renewals, "pp": pp_settings,
                "created_at": str(cat)[:16],
            })
        return scenarios
    except Exception as e:
        print(f"db_load_scenarios error: {e}")
        return []


def db_delete_scenario(conn, db_id):
    if not conn: return
    try:
        c = conn.cursor()
        c.execute("DELETE FROM mortgage_scenario_renewals WHERE scenario_id=?", db_id)
        c.execute("DELETE FROM mortgage_scenarios WHERE id=?", db_id)
        conn.commit()
    except Exception:
        pass


def db_update_scenario(conn, db_id, name, desc, renewals, pp_settings):
    """Update an existing scenario in-place (name, renewals, prepayment settings).
    Thin wrapper around db_save_scenario that always treats db_id as an update.
    Returns True on success, False on failure.
    """
    if not conn or not db_id:
        return False
    result = db_save_scenario(conn, db_id, name, desc or "", renewals, pp_settings)
    return result is not None
