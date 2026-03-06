# services/ai/custom/__init__.py
from flask import Blueprint

# 不要在這裡帶 url_prefix，讓 app 統一掛 /ai/custom
bp = Blueprint("custom", __name__)

# 匯入路由，讓裝飾器綁在 bp 上
from . import routes  # noqa: E402,F401