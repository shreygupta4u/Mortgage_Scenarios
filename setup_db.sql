-- Canadian Mortgage Analyzer — 3NF Schema v2
-- Run once against MortgageDB. Drops old JSON-blob tables and creates normalized schema.

-- Drop old JSON-based tables (user has confirmed data can be dropped)
IF EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_scenarios'      AND xtype='U') DROP TABLE mortgage_scenarios;
IF EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_past_renewals'  AND xtype='U') DROP TABLE mortgage_past_renewals;
IF EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_past_prepayments' AND xtype='U') DROP TABLE mortgage_past_prepayments;
IF EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_setup'          AND xtype='U') DROP TABLE mortgage_setup;
IF EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_scenario_renewals' AND xtype='U') DROP TABLE mortgage_scenario_renewals;

-- ── Core setup (single active row) ──────────────────────────────
CREATE TABLE mortgage_setup (
    id               INT IDENTITY PRIMARY KEY,
    purchase_price   DECIMAL(15,2)  NOT NULL,
    down_pct         DECIMAL(5,2)   NOT NULL DEFAULT 20.0,
    mortgage_type    NVARCHAR(20)   NOT NULL DEFAULT 'Fixed',
    pay_frequency    NVARCHAR(30)   NOT NULL DEFAULT 'Monthly',
    annual_rate      DECIMAL(7,4)   NOT NULL,
    amort_years      INT            NOT NULL DEFAULT 30,
    term_years       DECIMAL(4,1)   NOT NULL DEFAULT 3,
    start_date       DATE           NOT NULL,
    include_cmhc     BIT            NOT NULL DEFAULT 1,
    saved_at         DATETIME       DEFAULT GETDATE()
);

-- ── Past renewals (one row per renewal period) ─────────────────
CREATE TABLE mortgage_past_renewals (
    id               INT IDENTITY PRIMARY KEY,
    setup_id         INT NOT NULL REFERENCES mortgage_setup(id) ON DELETE CASCADE,
    seq_num          INT NOT NULL,
    start_date       DATE NOT NULL,
    annual_rate      DECIMAL(7,4) NOT NULL,
    mortgage_type    NVARCHAR(20) NOT NULL DEFAULT 'Fixed',
    term_years       DECIMAL(4,1) NOT NULL DEFAULT 3
);

-- ── Past prepayments (one row per prepayment event) ─────────────
CREATE TABLE mortgage_past_prepayments (
    id               INT IDENTITY PRIMARY KEY,
    setup_id         INT NOT NULL REFERENCES mortgage_setup(id) ON DELETE CASCADE,
    seq_num          INT NOT NULL,
    payment_date     DATE NOT NULL,
    amount           DECIMAL(15,2) NOT NULL DEFAULT 0
);

-- ── Scenarios header (rate-change + prepayment combined) ────────
CREATE TABLE mortgage_scenarios (
    id                   INT IDENTITY PRIMARY KEY,
    name                 NVARCHAR(200)  NOT NULL,
    description          NVARCHAR(2000) DEFAULT '',
    -- Prepayment settings (folded into scenario)
    annual_lump          DECIMAL(15,2)  DEFAULT 0,
    lump_month           INT            DEFAULT 1,       -- 1=Jan … 12=Dec
    lump_start_year      INT            DEFAULT 1,
    lump_num_years       INT            DEFAULT 0,
    pay_increase_type    NVARCHAR(20)   DEFAULT 'None',  -- None | Fixed | Pct
    pay_increase_val     DECIMAL(10,2)  DEFAULT 0,
    onetime_period       INT            DEFAULT 0,
    onetime_amount       DECIMAL(15,2)  DEFAULT 0,
    onetime_amount       DECIMAL(15,2)  DEFAULT 0,
    user_pmt             DECIMAL(10,2)  DEFAULT 0,    -- user-specified monthly payment
    created_at           DATETIME       DEFAULT GETDATE(),
    updated_at           DATETIME       DEFAULT GETDATE()
);

-- ── Renewals within a scenario (one row per renewal entry) ──────
CREATE TABLE mortgage_scenario_renewals (
    id               INT IDENTITY PRIMARY KEY,
    scenario_id      INT NOT NULL REFERENCES mortgage_scenarios(id) ON DELETE CASCADE,
    seq_num          INT NOT NULL,
    mode             NVARCHAR(20)  DEFAULT 'By Date',
    effective_date   DATE,
    effective_period INT           DEFAULT 1,
    new_rate         DECIMAL(7,4)  NOT NULL,
    mortgage_type    NVARCHAR(20)  DEFAULT 'Fixed',
    term_years       DECIMAL(4,1)  DEFAULT 3,
    actual_penalty   DECIMAL(15,2) DEFAULT 0,
    misc_fees        DECIMAL(15,2) DEFAULT 250,
    orig_posted_rate DECIMAL(7,4)  DEFAULT 0,
    curr_posted_rate DECIMAL(7,4)  DEFAULT 0,
    onetime_amount   DECIMAL(15,2) DEFAULT 0    -- per-renewal lump-sum
);

-- ── Prepayment scenarios (standalone strategies) ─────────────────
CREATE TABLE mortgage_prepay_scenarios (
    id                   INT IDENTITY PRIMARY KEY,
    name                 NVARCHAR(200)  NOT NULL,
    description          NVARCHAR(2000) DEFAULT '',
    annual_lump          DECIMAL(15,2)  DEFAULT 0,
    lump_month           INT            DEFAULT 1,
    lump_start_year      INT            DEFAULT 1,
    lump_num_years       INT            DEFAULT 0,
    pay_increase_type    NVARCHAR(20)   DEFAULT 'None',
    pay_increase_val     DECIMAL(10,2)  DEFAULT 0,
    onetime_period       INT            DEFAULT 0,
    onetime_amount       DECIMAL(15,2)  DEFAULT 0,
    created_at           DATETIME       DEFAULT GETDATE(),
    updated_at           DATETIME       DEFAULT GETDATE()
);
