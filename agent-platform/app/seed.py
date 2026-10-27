"""Initial agent configuration, taken from the RFP.

All real agents start *disabled* and in manual-approval mode; they are switched on
phase by phase. Only the ``demo`` agent is enabled so the pipeline can be tried
right after installation. Signatures, prompts and sources are filled in from the
dashboard (they are the client's data, not ours).
"""
from . import repo

TAX_POLICY = {
    "forbid_links": True,
    "forbid_mentions": True,
    "forbid_terms": ["دوره آموزشی", "ثبت‌نام دوره", "ثبت نام دوره", "تخفیف دوره"],
    "forbid_source_names": ["Accounting_House", "danestanihaiemalyati", "intapress"],
    "min_chars": 80,
    "semantic_dedup": True,
}
SENSITIVE_HEALTH = ["درمان قطعی", "بدون نیاز به پزشک", "معجزه", "دارو", "بیماری"]
SENSITIVE_FINANCE = ["سود قطعی", "تضمین سود", "بدون ریسک", "درآمد تضمینی"]

# (key, title, agent_type, channel kind, schedule, policy)
AGENTS = [
    ("seo", "سئو", "seo_article", "wordpress",
     [{"type": "weekly", "days": [1, 4], "times": ["10:00"], "task": "article"},
      {"type": "weekly", "days": [5], "times": ["09:00"], "task": "weekly_report"}], {}),
    ("bale_tax", "مؤدیان مالیاتی | بله", "news_rewrite", "bale",
     [{"type": "daily", "times": ["09:00"]}],
     {**TAX_POLICY, "topic": "اخبار و نکات مالیاتی برای مؤدیان"}),
    ("bale_accounting", "آموزش حسابداری | بله", "news_rewrite", "bale",
     [{"type": "daily", "times": ["10:00"]}],
     {**TAX_POLICY, "topic": "آموزش و نکات کاربردی حسابداری"}),
    ("doctor_regime", "دکتر رژیم | بله", "monitor", "bale",
     [{"type": "external"}], {}),
    ("fitplus", "فیت پلاس | بله", "monitor", "bale",
     [{"type": "external"}], {}),
    ("fitplus_daily", "فیت‌پلاس | پست روزانه بله", "fitplus_daily", "bale",
     [{"type": "daily", "times": ["10:00"]}],
     {"review_terms": SENSITIVE_HEALTH, "forbid_links": True, "channel_tag": "@fitplusapp"}),
    ("instagram_nutrition", "اینستاگرام تغذیه", "instagram_nutrition", "instagram",
     [{"type": "daily", "times": ["18:00"], "task": "reel"},
      {"type": "weekly", "days": [0, 2, 4], "times": ["12:00"], "task": "carousel"}],
     {"review_terms": SENSITIVE_HEALTH}),
    ("youtube_kids", "یوتیوب ۱ | کارتون کودک انگلیسی", "youtube_script", "youtube",
     [{"type": "every_n_days", "n": 2, "anchor": "2026-11-01", "times": ["17:00"]}],
     {"brief": "کانال کارتون کودک به زبان انگلیسی: سناریوی یک اپیزود کارتونی مناسب کودکان، با گفتگوی ساده انگلیسی، "
               "ارزش‌های آموزشی، بدون خشونت و ترساندن، با رعایت حقوق نشر (شخصیت و داستان اصیل) و سیاست محتوای کودک. "
               "عنوان و توضیحات به انگلیسی، دستور تولید به فارسی.", "minutes": 10}),
    ("youtube_health", "یوتیوب ۲ | رژیم و سلامت", "youtube_script", "youtube",
     [{"type": "every_n_days", "n": 2, "anchor": "2026-11-02", "times": ["17:00"]}],
     {"review_terms": SENSITIVE_HEALTH, "minutes": 12,
      "brief": "کانال آموزش رژیم، تغذیه، سلامتی و کاهش وزن به‌صورت آموزش عمومی؛ توصیه‌ها محتاطانه و قابل راستی‌آزمایی، "
               "بدون ادعای درمانی."}),
    ("youtube_finance", "یوتیوب ۳ | مالی و درآمد", "youtube_script", "youtube",
     [{"type": "every_n_days", "n": 2, "anchor": "2026-11-03", "times": ["17:00"]}],
     {"review_terms": SENSITIVE_FINANCE, "forbid_terms": ["سود قطعی", "تضمین سود"], "minutes": 12,
      "brief": "کانال آموزش نکات حسابداری، سرمایه‌گذاری و افزایش درآمد؛ ادعاها باید قابل راستی‌آزمایی باشند، "
               "هیچ وعده سود یا درآمد قطعی نده و ریسک‌ها را بگو."}),
    ("campaign_manager", "کمپین منیجر", "campaign", "sms",
     [{"type": "event"}], {}),
    ("virtual_city", "اینستاگرام | شهر مجازی", "virtual_city", "instagram",
     [{"type": "weekly", "days": [0, 2, 4], "times": ["19:00"], "task": "reel"},
      {"type": "daily", "times": ["11:00"], "task": "story"},
      {"type": "weekly", "days": [5], "times": ["09:00"], "task": "weekly_selection"}], {}),
]


def ensure(db, with_demo: bool = True) -> None:
    """Create missing agents/channels; never overwrite what the admin has edited."""
    for key, title, agent_type, ch_kind, schedule, policy in AGENTS:
        existing = repo.get_agent(db, key)
        if existing:
            if existing["agent_type"] == "pending" and agent_type != "pending":
                repo.update_agent(db, key, {"agent_type": agent_type}, actor=None)  # phase upgrade
            continue
        channel_id = repo.upsert_channel(db, ch_kind, f"{title} ({ch_kind})")
        repo.upsert_agent(db, key, title=title, agent_type=agent_type, enabled=False,
                          schedule=schedule, policy=policy, channel_id=channel_id,
                          publish_mode="manual_approval")
    if with_demo and not repo.get_agent(db, "demo"):
        channel_id = repo.upsert_channel(db, "log", "کانال آزمایشی (log)", signature="— کانال آزمایشی")
        repo.upsert_agent(db, "demo", title="ایجنت آزمایشی", agent_type="demo", enabled=True,
                          schedule=[], publish_mode="auto", channel_id=channel_id,
                          policy={"forbid_links": True})
