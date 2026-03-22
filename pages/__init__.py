"""
pages/__init__.py — Convenience re-exports so app.py can do `from pages import render_tab_X`.
"""
from pages.tab_setup       import render_tab_setup
from pages.tab_scenarios   import render_tab_scenarios
from pages.tab_prepayment  import render_tab_prepayment
from pages.tab_schedule    import render_tab_schedule
from pages.tab_comparison  import render_tab_comparison
