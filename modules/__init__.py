"""
modules/__init__.py — Convenience re-exports so any file can do `from modules import X`.
All symbols are sourced from their canonical submodules.
"""
from modules.mortgage_math import (
    FREQ, periodic_rate, calc_pmt, cmhc_premium,
    date_to_period, period_to_date, calc_remaining_years,
    build_amortization, get_today_metrics, calc_break_penalty,
)
from modules.mortgage_db import (
    get_db_connection, db_load_setup, db_save_setup,
    db_save_scenario, db_load_scenarios, db_delete_scenario,
    db_update_scenario,
)
from modules.mortgage_charts import stacked_bar_pi, _vline_x
from modules.mortgage_wireframe import generate_wireframe_docx
