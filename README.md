# 🏠 Canadian Mortgage Analyzer

A comprehensive Streamlit app for analyzing Canadian mortgage scenarios.

## Features

| Tab | What it does |
|---|---|
| 📊 Setup & Overview | Principal, CMHC insurance, stress test (B-20), GDS/TDS ratios |
| 📅 Amortization Schedule | Full period-by-period table — split principal/interest, downloadable CSV |
| 📈 Rate Change Scenarios | Model rate changes at any point; compare base vs new rates |
| 💰 Prepayment Analysis | Annual lump sums, increased regular payments, one-time payments + ROI |
| ⚠️ Break Penalty | 3-month interest, IRD calculator, break-even rate sweep chart |
| 🔄 Scenario Comparison | Up to 4 scenarios side by side with charts |
| 💾 Saved Scenarios | Save/load scenarios to MS SQL Server or local session |

## Canadian-Specific Accuracy

- ✅ **Semi-annual compounding** (as required by Canada's Interest Act)
- ✅ **CMHC mortgage insurance** (2.80%–4.00%) + PST/HST
- ✅ **B-20 Stress test** (contract rate + 2% or 5.25%, whichever is higher)
- ✅ **GDS / TDS ratios** (39% / 44% limits)
- ✅ **All payment frequencies** incl. accelerated bi-weekly & weekly
- ✅ **Break penalties**: 3 months interest (variable) or IRD (fixed)
- ✅ **Max amortization rules**: 25 yrs insured, 30 yrs conventional

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. (Optional) Set up MS SQL Server database

Run the SQL setup script in SSMS or sqlcmd:

```bash
sqlcmd -S localhost -E -i setup_db.sql
```

Or paste `setup_db.sql` into SQL Server Management Studio and execute.

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at **http://localhost:8501**

## Database Connection

In the sidebar:
1. Enter your SQL Server name (default: `localhost`)
2. Database name (default: `MortgageDB`)
3. Choose Windows Authentication or SQL auth
4. Click **🔌 Connect to DB**

If no DB is available, the app runs in **local mode** — all scenarios are saved to your browser session only.

## Requirements

- Python 3.9+
- SQL Server with ODBC Driver 17 (for DB features)
- Packages: `streamlit`, `pandas`, `numpy`, `plotly`, `python-dateutil`, `pyodbc`
