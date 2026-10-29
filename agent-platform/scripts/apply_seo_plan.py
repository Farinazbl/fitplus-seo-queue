"""One-off: apply the SEO plan to an existing database (seed never overwrites edits).

Usage:  python scripts/apply_seo_plan.py agents.db
4 articles/week (Sat Mon Wed Thu 10:00), weekly report Sunday 09:00, and the FitPlus Bale agent may
link its own site articles (https://fitplusapp.com/...).
"""
import sys
sys.path.insert(0, ".")
from app import repo
from app.db import DB

db = DB(sys.argv[1] if len(sys.argv) > 1 else "agents.db")
repo.update_agent(db, "seo", {"schedule": [
    {"type": "weekly", "days": [5, 0, 2, 3], "times": ["10:00"], "task": "article"},
    {"type": "weekly", "days": [6], "times": ["09:00"], "task": "weekly_report"}]}, "assistant")
a = repo.get_agent(db, "fitplus_daily")
repo.update_agent(db, "fitplus_daily", {"policy": {**a["policy"], "allowed_link_prefixes": ["https://fitplusapp.com/"]}}, "assistant")
print("ok")
