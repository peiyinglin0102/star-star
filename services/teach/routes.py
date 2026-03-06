# services/teach/routes.py
from __future__ import annotations
import os
from pathlib import Path
from flask import Blueprint, render_template

BASE = Path(__file__).resolve().parent
TPL_DIR = BASE.parent / "templates"   # -> services/templates
STATIC_DIR = BASE.parent / "static"   # -> services/static

bp = Blueprint(
    "teach",
    __name__,
    template_folder=str(TPL_DIR),
    static_folder=str(STATIC_DIR),
)

@bp.get("/")
def page():
    # 直接渲染 USER/Teach.html
    return render_template("USER/Teach.html")
