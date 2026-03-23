"""mortgage_db.py — DB helpers: MS SQL + SQLite. Variable sub-scenarios + terminal renewals."""
import os, uuid as _uuid_mod, sqlite3

class _Conn:
    def __init__(self, raw, db_type, label=""):
        self._raw = raw; self.db_type = db_type; self.label = label
    def cursor(self):  return self._raw.cursor()
    def commit(self):  self._raw.commit()
    def close(self):   self._raw.close()

def _last_id(cursor, conn):
    if conn.db_type == "sqlite": return cursor.lastrowid
    cursor.execute("SELECT @@IDENTITY"); return int(cursor.fetchone()[0])

def _ifnull(conn, expr, default="0"):
    return f"IFNULL({expr},{default})" if conn.db_type=="sqlite" else f"ISNULL({expr},{default})"

def _top1(conn, cols, table, order=""):
    tail = f"ORDER BY {order} LIMIT 1" if conn.db_type=="sqlite" else ""
    if conn.db_type=="sqlite":
        return f"SELECT {cols} FROM {table} {tail}"
    return f"SELECT TOP 1 {cols} FROM {table} {'ORDER BY '+order if order else ''}"

def _gd(conn): return "datetime('now')" if conn.db_type=="sqlite" else "GETDATE()"
def _ai(conn): return "INTEGER PRIMARY KEY AUTOINCREMENT" if conn.db_type=="sqlite" else "INT IDENTITY PRIMARY KEY"

def get_db_connection(server, database, trusted, user="", pwd=""):
    try:
        import pyodbc
        cs = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};"
              f"DATABASE={database};"
              + ("Trusted_Connection=yes;" if trusted else f"UID={user};PWD={pwd};"))
        raw  = pyodbc.connect(cs, timeout=5)
        conn = _Conn(raw, "mssql", f"{server}/{database}")
        _init_db(conn); return conn, None
    except Exception as e: return None, str(e)

def get_sqlite_connection(path=None):
    try:
        if not path: path = os.path.join(os.getcwd(), "mortgage_local.db")
        raw  = sqlite3.connect(path, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        conn = _Conn(raw, "sqlite", path)
        _init_db(conn); return conn, None
    except Exception as e: return None, str(e)

# ── DDL ───────────────────────────────────────────────────────────
def _tables_exist(conn):
    try:
        c = conn.cursor()
        if conn.db_type == "sqlite":
            c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mortgage_setup'")
        else:
            c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='mortgage_setup'")
        row = c.fetchone(); return (row[0] if row else 0) > 0
    except Exception: return False

def _create_tables(conn):
    ai = _ai(conn); gd = _gd(conn); c = conn.cursor()
    if conn.db_type == "sqlite":
        stmts = [
            f"CREATE TABLE IF NOT EXISTS mortgage_setup(id {ai},purchase_price REAL NOT NULL,down_pct REAL NOT NULL DEFAULT 20,mortgage_type TEXT NOT NULL DEFAULT 'Fixed',pay_frequency TEXT NOT NULL DEFAULT 'Monthly',annual_rate REAL NOT NULL,amort_years INTEGER NOT NULL DEFAULT 30,term_years REAL NOT NULL DEFAULT 3,start_date TEXT NOT NULL,include_cmhc INTEGER NOT NULL DEFAULT 1,saved_at TEXT DEFAULT ({gd}))",
            f"CREATE TABLE IF NOT EXISTS mortgage_past_renewals(id {ai},setup_id INTEGER,seq_num INTEGER,start_date TEXT,annual_rate REAL,mortgage_type TEXT,term_years REAL)",
            f"CREATE TABLE IF NOT EXISTS mortgage_past_prepayments(id {ai},setup_id INTEGER,seq_num INTEGER,payment_date TEXT,amount REAL)",
            f"CREATE TABLE IF NOT EXISTS mortgage_scenarios(id {ai},name TEXT,description TEXT DEFAULT '',annual_lump REAL DEFAULT 0,lump_month INTEGER DEFAULT 1,lump_start_year INTEGER DEFAULT 1,lump_num_years INTEGER DEFAULT 0,pay_increase_type TEXT DEFAULT 'None',pay_increase_val REAL DEFAULT 0,onetime_period INTEGER DEFAULT 0,onetime_amount REAL DEFAULT 0,user_pmt REAL DEFAULT 0,linked_pp_db_id INTEGER DEFAULT 0,created_at TEXT DEFAULT ({gd}),updated_at TEXT DEFAULT ({gd}))",
            f"CREATE TABLE IF NOT EXISTS mortgage_scenario_renewals(id {ai},scenario_id INTEGER,seq_num INTEGER,mode TEXT DEFAULT 'By Date',effective_date TEXT,effective_period INTEGER DEFAULT 1,new_rate REAL,mortgage_type TEXT DEFAULT 'Fixed',term_years REAL DEFAULT 3,actual_penalty REAL DEFAULT 0,misc_fees REAL DEFAULT 250,orig_posted_rate REAL DEFAULT 0,curr_posted_rate REAL DEFAULT 0,onetime_amount REAL DEFAULT 0,is_terminal INTEGER DEFAULT 0)",
            f"CREATE TABLE IF NOT EXISTS mortgage_scenario_variable_subs(id {ai},scenario_id INTEGER NOT NULL,renewal_seq_num INTEGER NOT NULL,sub_seq INTEGER NOT NULL,effective_date TEXT NOT NULL,annual_rate REAL NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS mortgage_prepay_scenarios(id {ai},name TEXT NOT NULL,description TEXT DEFAULT '',annual_lump REAL DEFAULT 0,lump_month INTEGER DEFAULT 1,lump_start_year INTEGER DEFAULT 1,lump_num_years INTEGER DEFAULT 0,pay_increase_type TEXT DEFAULT 'None',pay_increase_val REAL DEFAULT 0,onetime_period INTEGER DEFAULT 0,onetime_amount REAL DEFAULT 0,created_at TEXT DEFAULT ({gd}),updated_at TEXT DEFAULT ({gd}))",
        ]
    else:
        stmts = [
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_setup' AND xtype='U') CREATE TABLE mortgage_setup(id INT IDENTITY PRIMARY KEY,purchase_price DECIMAL(15,2) NOT NULL,down_pct DECIMAL(5,2) NOT NULL DEFAULT 20,mortgage_type NVARCHAR(20) NOT NULL DEFAULT 'Fixed',pay_frequency NVARCHAR(30) NOT NULL DEFAULT 'Monthly',annual_rate DECIMAL(7,4) NOT NULL,amort_years INT NOT NULL DEFAULT 30,term_years DECIMAL(4,1) NOT NULL DEFAULT 3,start_date DATE NOT NULL,include_cmhc BIT NOT NULL DEFAULT 1,saved_at DATETIME DEFAULT GETDATE())",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_past_renewals' AND xtype='U') CREATE TABLE mortgage_past_renewals(id INT IDENTITY PRIMARY KEY,setup_id INT,seq_num INT,start_date DATE,annual_rate DECIMAL(7,4),mortgage_type NVARCHAR(20),term_years DECIMAL(4,1))",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_past_prepayments' AND xtype='U') CREATE TABLE mortgage_past_prepayments(id INT IDENTITY PRIMARY KEY,setup_id INT,seq_num INT,payment_date DATE,amount DECIMAL(15,2))",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U') CREATE TABLE mortgage_scenarios(id INT IDENTITY PRIMARY KEY,name NVARCHAR(200),description NVARCHAR(2000) DEFAULT '',annual_lump DECIMAL(15,2) DEFAULT 0,lump_month INT DEFAULT 1,lump_start_year INT DEFAULT 1,lump_num_years INT DEFAULT 0,pay_increase_type NVARCHAR(20) DEFAULT 'None',pay_increase_val DECIMAL(10,2) DEFAULT 0,onetime_period INT DEFAULT 0,onetime_amount DECIMAL(15,2) DEFAULT 0,user_pmt DECIMAL(10,2) DEFAULT 0,linked_pp_db_id INT DEFAULT 0,created_at DATETIME DEFAULT GETDATE(),updated_at DATETIME DEFAULT GETDATE())",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenario_renewals' AND xtype='U') CREATE TABLE mortgage_scenario_renewals(id INT IDENTITY PRIMARY KEY,scenario_id INT,seq_num INT,mode NVARCHAR(20) DEFAULT 'By Date',effective_date DATE,effective_period INT DEFAULT 1,new_rate DECIMAL(7,4),mortgage_type NVARCHAR(20) DEFAULT 'Fixed',term_years DECIMAL(4,1) DEFAULT 3,actual_penalty DECIMAL(15,2) DEFAULT 0,misc_fees DECIMAL(15,2) DEFAULT 250,orig_posted_rate DECIMAL(7,4) DEFAULT 0,curr_posted_rate DECIMAL(7,4) DEFAULT 0,onetime_amount DECIMAL(15,2) DEFAULT 0,is_terminal BIT DEFAULT 0)",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenario_variable_subs' AND xtype='U') CREATE TABLE mortgage_scenario_variable_subs(id INT IDENTITY PRIMARY KEY,scenario_id INT NOT NULL,renewal_seq_num INT NOT NULL,sub_seq INT NOT NULL,effective_date DATE NOT NULL,annual_rate DECIMAL(7,4) NOT NULL)",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_prepay_scenarios' AND xtype='U') CREATE TABLE mortgage_prepay_scenarios(id INT IDENTITY PRIMARY KEY,name NVARCHAR(200) NOT NULL,description NVARCHAR(2000) DEFAULT '',annual_lump DECIMAL(15,2) DEFAULT 0,lump_month INT DEFAULT 1,lump_start_year INT DEFAULT 1,lump_num_years INT DEFAULT 0,pay_increase_type NVARCHAR(20) DEFAULT 'None',pay_increase_val DECIMAL(10,2) DEFAULT 0,onetime_period INT DEFAULT 0,onetime_amount DECIMAL(15,2) DEFAULT 0,created_at DATETIME DEFAULT GETDATE(),updated_at DATETIME DEFAULT GETDATE())",
        ]
    for sql in stmts:
        try: c.execute(sql)
        except Exception: pass
    conn.commit()

def _run_migrations(conn):
    c = conn.cursor()
    if conn.db_type == "sqlite":
        migs = [
            "ALTER TABLE mortgage_scenario_renewals ADD COLUMN onetime_amount REAL DEFAULT 0",
            "ALTER TABLE mortgage_scenario_renewals ADD COLUMN is_terminal INTEGER DEFAULT 0",
            "ALTER TABLE mortgage_scenarios ADD COLUMN user_pmt REAL DEFAULT 0",
            "ALTER TABLE mortgage_scenarios ADD COLUMN linked_pp_db_id INTEGER DEFAULT 0",
            f"CREATE TABLE IF NOT EXISTS mortgage_prepay_scenarios(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,description TEXT DEFAULT '',annual_lump REAL DEFAULT 0,lump_month INTEGER DEFAULT 1,lump_start_year INTEGER DEFAULT 1,lump_num_years INTEGER DEFAULT 0,pay_increase_type TEXT DEFAULT 'None',pay_increase_val REAL DEFAULT 0,onetime_period INTEGER DEFAULT 0,onetime_amount REAL DEFAULT 0,created_at TEXT DEFAULT (datetime('now')),updated_at TEXT DEFAULT (datetime('now')))",
            f"CREATE TABLE IF NOT EXISTS mortgage_scenario_variable_subs(id INTEGER PRIMARY KEY AUTOINCREMENT,scenario_id INTEGER NOT NULL,renewal_seq_num INTEGER NOT NULL,sub_seq INTEGER NOT NULL,effective_date TEXT NOT NULL,annual_rate REAL NOT NULL)",
        ]
        for sql in migs:
            try: c.execute(sql)
            except Exception: pass
    else:
        migs = [
            "IF NOT EXISTS(SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('mortgage_scenario_renewals') AND name='onetime_amount') ALTER TABLE mortgage_scenario_renewals ADD onetime_amount DECIMAL(15,2) DEFAULT 0",
            "IF NOT EXISTS(SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('mortgage_scenario_renewals') AND name='is_terminal') ALTER TABLE mortgage_scenario_renewals ADD is_terminal BIT DEFAULT 0",
            "IF NOT EXISTS(SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('mortgage_scenarios') AND name='user_pmt') ALTER TABLE mortgage_scenarios ADD user_pmt DECIMAL(10,2) DEFAULT 0",
            "IF NOT EXISTS(SELECT * FROM sys.columns WHERE object_id=OBJECT_ID('mortgage_scenarios') AND name='linked_pp_db_id') ALTER TABLE mortgage_scenarios ADD linked_pp_db_id INT DEFAULT 0",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_prepay_scenarios' AND xtype='U') CREATE TABLE mortgage_prepay_scenarios(id INT IDENTITY PRIMARY KEY,name NVARCHAR(200) NOT NULL,description NVARCHAR(2000) DEFAULT '',annual_lump DECIMAL(15,2) DEFAULT 0,lump_month INT DEFAULT 1,lump_start_year INT DEFAULT 1,lump_num_years INT DEFAULT 0,pay_increase_type NVARCHAR(20) DEFAULT 'None',pay_increase_val DECIMAL(10,2) DEFAULT 0,onetime_period INT DEFAULT 0,onetime_amount DECIMAL(15,2) DEFAULT 0,created_at DATETIME DEFAULT GETDATE(),updated_at DATETIME DEFAULT GETDATE())",
            "IF NOT EXISTS(SELECT * FROM sysobjects WHERE name='mortgage_scenario_variable_subs' AND xtype='U') CREATE TABLE mortgage_scenario_variable_subs(id INT IDENTITY PRIMARY KEY,scenario_id INT NOT NULL,renewal_seq_num INT NOT NULL,sub_seq INT NOT NULL,effective_date DATE NOT NULL,annual_rate DECIMAL(7,4) NOT NULL)",
        ]
        for sql in migs:
            try: c.execute(sql)
            except Exception: pass
    conn.commit()

def _run_sql_file(conn, path):
    with open(path) as f: sql = f.read()
    c = conn.cursor()
    for batch in sql.split("\nGO"):
        batch = batch.strip()
        if batch and not batch.startswith("--"):
            try: c.execute(batch)
            except Exception: pass
    conn.commit()

def _init_db(conn):
    if _tables_exist(conn): _run_migrations(conn); return
    if conn.db_type == "mssql":
        this_dir = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(os.path.dirname(this_dir),"setup_db.sql"),
                  os.path.join(this_dir,"setup_db.sql"),os.path.join(os.getcwd(),"setup_db.sql")]:
            if os.path.exists(p): _run_sql_file(conn,p); _run_migrations(conn); return
    _create_tables(conn)

# ── Setup ─────────────────────────────────────────────────────────
def db_load_setup(conn):
    if not conn: return None
    try:
        c = conn.cursor()
        sql = _top1(conn,"id,purchase_price,down_pct,mortgage_type,pay_frequency,annual_rate,amort_years,term_years,start_date,include_cmhc","mortgage_setup","id DESC")
        c.execute(sql); row = c.fetchone()
        if not row: return None
        sid,pp,dpct,mt,pf,ar,ay,ty,sd,ic = row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9]
        c.execute("SELECT seq_num,start_date,annual_rate,mortgage_type,term_years FROM mortgage_past_renewals WHERE setup_id=? ORDER BY seq_num",(sid,))
        past_renewals = [{"id":f"db_{r[0]}","start_date_str":str(r[1]),"rate":float(r[2]),"mtype":r[3],"term_years":float(r[4])} for r in c.fetchall()]
        c.execute("SELECT seq_num,payment_date,amount FROM mortgage_past_prepayments WHERE setup_id=? ORDER BY seq_num",(sid,))
        past_prepayments = [{"id":f"db_{r[0]}","date_str":str(r[1]),"amount":float(r[2])} for r in c.fetchall()]
        return {"widget_state":{"s_price":float(pp),"s_dpct":float(dpct),"s_mtype":mt,"s_freq":pf,"s_rate":float(ar),"s_amort":int(ay),"s_term":float(ty),"s_startdate":str(sd),"s_addcmhc":bool(ic)},
                "past_renewals":past_renewals,"past_prepayments":past_prepayments,"_setup_id":sid}
    except Exception: return None

def db_save_setup(conn, data):
    if not conn: return False
    try:
        c = conn.cursor(); ws = data.get("widget_state",{})
        c.execute("DELETE FROM mortgage_past_prepayments WHERE setup_id IN (SELECT id FROM mortgage_setup)")
        c.execute("DELETE FROM mortgage_past_renewals WHERE setup_id IN (SELECT id FROM mortgage_setup)")
        c.execute("DELETE FROM mortgage_setup")
        sd_str = str(ws.get("s_startdate","2023-08-15"))
        c.execute("INSERT INTO mortgage_setup(purchase_price,down_pct,mortgage_type,pay_frequency,annual_rate,amort_years,term_years,start_date,include_cmhc) VALUES(?,?,?,?,?,?,?,?,?)",
                  (float(ws.get("s_price",1030000)),float(ws.get("s_dpct",20)),ws.get("s_mtype","Fixed"),ws.get("s_freq","Monthly"),float(ws.get("s_rate",5.39)),int(ws.get("s_amort",30)),float(ws.get("s_term",3)),sd_str,int(bool(ws.get("s_addcmhc",True)))))
        setup_id = _last_id(c, conn)
        for i,rn in enumerate(data.get("past_renewals",[]),1):
            c.execute("INSERT INTO mortgage_past_renewals(setup_id,seq_num,start_date,annual_rate,mortgage_type,term_years) VALUES(?,?,?,?,?,?)",(setup_id,i,str(rn["start_date_str"]),float(rn["rate"]),rn["mtype"],float(rn["term_years"])))
        for i,pp in enumerate(data.get("past_prepayments",[]),1):
            c.execute("INSERT INTO mortgage_past_prepayments(setup_id,seq_num,payment_date,amount) VALUES(?,?,?,?)",(setup_id,i,str(pp["date_str"]),float(pp["amount"])))
        conn.commit(); return True
    except Exception as e: print(f"db_save_setup error: {e}"); return False

# ── Rate-change Scenarios ─────────────────────────────────────────
def db_save_scenario(conn, sc_id_or_none, name, desc, renewals, pp_settings, user_pmt=0, linked_pp_db_id=0):
    if not conn: return None
    try:
        c=conn.cursor(); pp=pp_settings; upmt=float(user_pmt or 0); lppid=int(linked_pp_db_id or 0); gd=_gd(conn)
        if sc_id_or_none:
            c.execute(f"UPDATE mortgage_scenarios SET name=?,description=?,annual_lump=?,lump_month=?,lump_start_year=?,lump_num_years=?,pay_increase_type=?,pay_increase_val=?,onetime_period=?,onetime_amount=?,user_pmt=?,linked_pp_db_id=?,updated_at={gd} WHERE id=?",
                      (name,desc,float(pp.get("annual_lump",0)),int(pp.get("lump_month",1)),int(pp.get("lump_start_year",1)),int(pp.get("lump_num_years",0)),pp.get("pay_increase_type","None"),float(pp.get("pay_increase_val",0)),int(pp.get("onetime_period",0)),float(pp.get("onetime_amount",0)),upmt,lppid,sc_id_or_none))
            db_id = sc_id_or_none
            c.execute("DELETE FROM mortgage_scenario_variable_subs WHERE scenario_id=?",(db_id,))
            c.execute("DELETE FROM mortgage_scenario_renewals WHERE scenario_id=?",(db_id,))
        else:
            c.execute("INSERT INTO mortgage_scenarios(name,description,annual_lump,lump_month,lump_start_year,lump_num_years,pay_increase_type,pay_increase_val,onetime_period,onetime_amount,user_pmt,linked_pp_db_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (name,desc,float(pp.get("annual_lump",0)),int(pp.get("lump_month",1)),int(pp.get("lump_start_year",1)),int(pp.get("lump_num_years",0)),pp.get("pay_increase_type","None"),float(pp.get("pay_increase_val",0)),int(pp.get("onetime_period",0)),float(pp.get("onetime_amount",0)),upmt,lppid))
            db_id = _last_id(c, conn)
        for i,rn in enumerate(renewals,1):
            eff_date = rn.get("date_str")
            c.execute("INSERT INTO mortgage_scenario_renewals(scenario_id,seq_num,mode,effective_date,effective_period,new_rate,mortgage_type,term_years,actual_penalty,misc_fees,orig_posted_rate,curr_posted_rate,onetime_amount,is_terminal) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (db_id,i,rn.get("mode","By Date"),str(eff_date) if eff_date else None,int(rn.get("period",1)),
                       float(rn["new_rate"]),rn.get("mtype","Fixed"),float(rn.get("term_years",3)),
                       float(rn.get("actual_penalty",0)),float(rn.get("misc_fees",250)),
                       float(rn.get("orig_posted",0)),float(rn.get("curr_posted",0)),
                       float(rn.get("onetime_amount",0)),int(bool(rn.get("is_terminal",False)))))
            # Save variable sub-scenarios
            for si, sub in enumerate(rn.get("variable_subs",[])):
                if sub.get("date_str") and sub.get("rate"):
                    c.execute("INSERT INTO mortgage_scenario_variable_subs(scenario_id,renewal_seq_num,sub_seq,effective_date,annual_rate) VALUES(?,?,?,?,?)",
                              (db_id,i,si,str(sub["date_str"]),float(sub["rate"])))
        conn.commit(); return db_id
    except Exception as e: print(f"db_save_scenario error: {e}"); return None

def db_load_scenarios(conn):
    if not conn: return []
    try:
        ifn = lambda x: _ifnull(conn,x)
        c=conn.cursor()
        c.execute(f"SELECT id,name,description,annual_lump,lump_month,lump_start_year,lump_num_years,pay_increase_type,pay_increase_val,onetime_period,onetime_amount,{ifn('user_pmt')},{ifn('linked_pp_db_id')},created_at FROM mortgage_scenarios ORDER BY id")
        scenarios=[]
        for row in c.fetchall():
            sid,name,desc,al,lm,lsy,lny,pit,piv,otp,ota,upmt,lppid,cat = row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9],row[10],row[11],row[12],row[13]
            c2=conn.cursor()
            c2.execute(f"SELECT seq_num,mode,effective_date,effective_period,new_rate,mortgage_type,term_years,actual_penalty,misc_fees,orig_posted_rate,curr_posted_rate,{ifn('onetime_amount')},{ifn('is_terminal')} FROM mortgage_scenario_renewals WHERE scenario_id=? ORDER BY seq_num",(sid,))
            renewals=[]
            for r in c2.fetchall():
                sn,mode,edate,eper,nr,mt,ty,ap,mf,op,cp,ren_ota,is_term = r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10],r[11],r[12]
                # Load variable sub-scenarios for this renewal
                c3=conn.cursor()
                c3.execute("SELECT sub_seq,effective_date,annual_rate FROM mortgage_scenario_variable_subs WHERE scenario_id=? AND renewal_seq_num=? ORDER BY sub_seq",(sid,sn))
                vsubs=[{"id":str(_uuid_mod.uuid4())[:8],"date_str":str(vr[1]),"rate":float(vr[2])} for vr in c3.fetchall()]
                renewals.append({
                    "id":str(_uuid_mod.uuid4())[:8],"mode":mode or "By Date",
                    "date_str":str(edate) if edate else None,
                    "period":int(eper) if eper else 1,"new_rate":float(nr),
                    "mtype":mt or "Fixed","term_years":float(ty) if ty else 3,
                    "actual_penalty":float(ap) if ap else 0,"misc_fees":float(mf) if mf else 250,
                    "orig_posted":float(op) if op else 0,"curr_posted":float(cp) if cp else 0,
                    "onetime_amount":float(ren_ota) if ren_ota else 0,
                    "is_terminal":bool(int(is_term or 0)),
                    "variable_subs":vsubs,
                })
            scenarios.append({
                "db_id":sid,"name":name,"desc":desc or "","renewals":renewals,
                "pp":{"annual_lump":float(al or 0),"lump_month":int(lm or 1),"lump_start_year":int(lsy or 1),"lump_num_years":int(lny or 0),"pay_increase_type":pit or "None","pay_increase_val":float(piv or 0),"onetime_period":int(otp or 0),"onetime_amount":float(ota or 0)},
                "user_pmt":float(upmt or 0),"linked_pp_db_id":int(lppid or 0),"created_at":str(cat)[:16],
            })
        return scenarios
    except Exception as e: print(f"db_load_scenarios error: {e}"); return []

def db_delete_scenario(conn, db_id):
    if not conn: return
    try:
        c=conn.cursor()
        c.execute("DELETE FROM mortgage_scenario_variable_subs WHERE scenario_id=?",(db_id,))
        c.execute("DELETE FROM mortgage_scenario_renewals WHERE scenario_id=?",(db_id,))
        c.execute("DELETE FROM mortgage_scenarios WHERE id=?",(db_id,))
        conn.commit()
    except Exception: pass

def db_update_scenario(conn, db_id, name, desc, renewals, pp_settings, user_pmt=0, linked_pp_db_id=0):
    if not conn or not db_id: return False
    return db_save_scenario(conn,db_id,name,desc or "",renewals,pp_settings,user_pmt,linked_pp_db_id) is not None

# ── Prepayment Scenarios ──────────────────────────────────────────
def db_save_prepay_scenario(conn, sc_id_or_none, name, desc, settings):
    if not conn: return None
    try:
        c=conn.cursor(); s=settings; gd=_gd(conn)
        if sc_id_or_none:
            c.execute(f"UPDATE mortgage_prepay_scenarios SET name=?,description=?,annual_lump=?,lump_month=?,lump_start_year=?,lump_num_years=?,pay_increase_type=?,pay_increase_val=?,onetime_period=?,onetime_amount=?,updated_at={gd} WHERE id=?",
                      (name,desc,float(s.get("annual_lump",0)),int(s.get("lump_month",1)),int(s.get("lump_start_year",1)),int(s.get("lump_num_years",0)),s.get("pay_increase_type","None"),float(s.get("pay_increase_val",0)),int(s.get("onetime_period",0)),float(s.get("onetime_amount",0)),sc_id_or_none))
            conn.commit(); return sc_id_or_none
        else:
            c.execute("INSERT INTO mortgage_prepay_scenarios(name,description,annual_lump,lump_month,lump_start_year,lump_num_years,pay_increase_type,pay_increase_val,onetime_period,onetime_amount) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (name,desc,float(s.get("annual_lump",0)),int(s.get("lump_month",1)),int(s.get("lump_start_year",1)),int(s.get("lump_num_years",0)),s.get("pay_increase_type","None"),float(s.get("pay_increase_val",0)),int(s.get("onetime_period",0)),float(s.get("onetime_amount",0))))
            db_id=_last_id(c,conn); conn.commit(); return db_id
    except Exception as e: print(f"db_save_prepay_scenario error: {e}"); return None

def db_load_prepay_scenarios(conn):
    if not conn: return []
    try:
        c=conn.cursor()
        c.execute("SELECT id,name,description,annual_lump,lump_month,lump_start_year,lump_num_years,pay_increase_type,pay_increase_val,onetime_period,onetime_amount,created_at FROM mortgage_prepay_scenarios ORDER BY id")
        result=[]
        for row in c.fetchall():
            sid,name,desc,al,lm,lsy,lny,pit,piv,otp,ota,cat = row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9],row[10],row[11]
            result.append({"db_id":sid,"name":name,"desc":desc or "","settings":{"annual_lump":float(al or 0),"lump_month":int(lm or 1),"lump_start_year":int(lsy or 1),"lump_num_years":int(lny or 0),"pay_increase_type":pit or "None","pay_increase_val":float(piv or 0),"onetime_period":int(otp or 0),"onetime_amount":float(ota or 0)},"created_at":str(cat)[:16]})
        return result
    except Exception as e: print(f"db_load_prepay_scenarios error: {e}"); return []

def db_delete_prepay_scenario(conn, db_id):
    if not conn: return
    try:
        c=conn.cursor(); c.execute("DELETE FROM mortgage_prepay_scenarios WHERE id=?",(db_id,)); conn.commit()
    except Exception: pass
