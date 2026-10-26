"""ASGI entry point: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``."""
from . import config
from .api import create_app

_settings = config.from_env()
if not _settings.tokens:
    raise SystemExit("APP_TOKENS تنظیم نشده است. نمونه: APP_TOKENS='admin=<توکن ۱۶+ نویسه>:admin'")
app = create_app(_settings)
