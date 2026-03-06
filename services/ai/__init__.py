from flask import Blueprint
bp = Blueprint("ai", __name__)   # 模板走共用 app 的 template_folder
from . import routes  # noqa

