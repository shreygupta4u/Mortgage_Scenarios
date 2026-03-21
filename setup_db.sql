-- Canadian Mortgage Analyzer — Database Setup
-- Run this script against your MortgageDB to create required tables.
-- Safe to run multiple times (IF NOT EXISTS guards).

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_setup' AND xtype='U')
BEGIN
    CREATE TABLE mortgage_setup (
        id          INT IDENTITY PRIMARY KEY,
        saved_at    DATETIME DEFAULT GETDATE(),
        setup_data  NVARCHAR(MAX)
    );
    PRINT 'Created table: mortgage_setup';
END
ELSE
    PRINT 'Table already exists: mortgage_setup';

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U')
BEGIN
    CREATE TABLE mortgage_scenarios (
        id          INT IDENTITY PRIMARY KEY,
        name        NVARCHAR(200),
        created_at  DATETIME DEFAULT GETDATE(),
        params      NVARCHAR(MAX),
        summary     NVARCHAR(MAX)
    );
    PRINT 'Created table: mortgage_scenarios';
END
ELSE
    PRINT 'Table already exists: mortgage_scenarios';
