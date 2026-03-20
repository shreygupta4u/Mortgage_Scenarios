-- ============================================================
-- Canadian Mortgage Analyzer — MS SQL Server Setup Script
-- Run this in SSMS or sqlcmd against your local SQL Server
-- ============================================================

-- 1. Create the database (skip if it already exists)
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'MortgageDB')
BEGIN
    CREATE DATABASE MortgageDB;
    PRINT 'MortgageDB created.';
END
GO

USE MortgageDB;
GO

-- 2. Mortgage scenarios table
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U')
BEGIN
    CREATE TABLE mortgage_scenarios (
        id          INT IDENTITY(1,1) PRIMARY KEY,
        name        NVARCHAR(200)     NOT NULL,
        created_at  DATETIME          DEFAULT GETDATE(),
        params      NVARCHAR(MAX),    -- JSON: all input parameters
        summary     NVARCHAR(MAX)     -- JSON: key result metrics
    );
    PRINT 'Table mortgage_scenarios created.';
END
GO

-- 3. Optional: rate history table for your own rate tracking
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='rate_history' AND xtype='U')
BEGIN
    CREATE TABLE rate_history (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        recorded_date   DATE          NOT NULL DEFAULT CAST(GETDATE() AS DATE),
        boc_policy_rate DECIMAL(5,3),
        prime_rate      DECIMAL(5,3),
        fixed_5yr       DECIMAL(5,3),
        variable_rate   DECIMAL(5,3),
        notes           NVARCHAR(500)
    );
    -- Seed with some recent approximate rates for reference
    INSERT INTO rate_history (recorded_date, boc_policy_rate, prime_rate, fixed_5yr, variable_rate, notes)
    VALUES
        ('2024-01-01', 5.00, 7.20, 5.04, 6.95, 'Start of 2024'),
        ('2024-06-01', 5.00, 7.20, 4.99, 6.95, 'Pre first cut'),
        ('2024-09-01', 4.25, 6.45, 4.69, 6.20, 'Post Sept cut'),
        ('2024-12-01', 3.25, 5.45, 4.34, 5.20, 'Dec 2024'),
        ('2025-03-01', 2.75, 4.95, 4.19, 4.70, 'Mar 2025 est');
    PRINT 'Table rate_history created and seeded.';
END
GO

-- 4. Verify
SELECT 'mortgage_scenarios' AS TableName, COUNT(*) AS Rows FROM mortgage_scenarios
UNION ALL
SELECT 'rate_history',                     COUNT(*)          FROM rate_history;
GO

PRINT 'Setup complete. Connect your Streamlit app to MortgageDB.';
