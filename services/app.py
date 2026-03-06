# services/app.py  (MAIN @ 18361 / HTTPS)
from __future__ import annotations
import sys, os, logging
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, render_template, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

# ---------- 路徑 ----------
SERV = Path(__file__).resolve().parent
ROOT = SERV.parent
SERV_TPL = SERV / "templates"
SERV_STATIC = SERV / "static"
USER_DIR = SERV_TPL / "USER"

# ---------- 載入環境變數 ----------
load_dotenv(dotenv_path=ROOT / ".env")


# ---------- sys.path ----------
for pth in (SERV, ROOT, SERV / "ai"):
    s_ = str(pth)
    if s_ not in sys.path:
        sys.path.insert(0, s_)

# ---------- 建立 Flask ----------
app = Flask(__name__, template_folder=str(SERV_TPL), static_folder=str(SERV_STATIC))
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "main-secret-key-456")
app.config["SESSION_COOKIE_NAME"] = "star_main_session"
app.config["SESSION_COOKIE_SECURE"] = True         # ✅ MAIN 維持 HTTPS Secure
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PREFERRED_URL_SCHEME"] = "https"
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# ===================== 初始化核心套件 =====================
from auth import db, mail, migrate
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///app.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "True").lower() == "true",
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "False").lower() == "true",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER"),
    SECURITY_PASSWORD_SALT=os.getenv("SECURITY_PASSWORD_SALT", "dev_salt"),
    RESET_PASSWORD_SALT=os.getenv("RESET_PASSWORD_SALT", "dev_reset_salt"),
    TOKEN_MAX_AGE=60 * 2,
    RESET_TOKEN_MAX_AGE=60 * 60,
    DEV_PRINT_VERIFY_LINK=os.getenv("DEV_PRINT_VERIFY_LINK", "True").lower() == "true",
)
db.init_app(app); mail.init_app(app); migrate.init_app(app, db)

# ===================== 掛藍圖（順序很重要） =====================
from auth.routes import bp as auth_bp, api_register as _api_register, api_verify_otp as _api_verify_otp
from auth.admin_routes import bp as admin_bp, ensure_default_admin
from services.quiz.quiz import bp as quiz_bp
from services.quiz.learn import learn_bp
from services.game.routes import bp as game_bp
from services.teach.routes import bp as teach_bp
app.register_blueprint(teach_bp, url_prefix="/teach")
app.register_blueprint(game_bp)
app.register_blueprint(learn_bp)
app.register_blueprint(auth_bp,  url_prefix="/auth")
app.register_blueprint(admin_bp, url_prefix="/admin")
app.register_blueprint(quiz_bp,  url_prefix="/quiz")

# --- ensure /ai/custom blueprint is mounted ---
try:
    # 把 import 放在 try 裡：任何錯誤都不要在 module import 階段炸掉
    from services.ai.custom.routes import bp as custom_bp
    app.register_blueprint(custom_bp, url_prefix="/ai/custom")
    app.logger.info("[custom] blueprint registered at /ai/custom")
except Exception as e:
    app.logger.exception("[custom] failed to register blueprint: %s", e)
    # 兜底，至少這三頁可用
    app.add_url_rule('/ai/custom/',  endpoint='custom.home',      view_func=lambda: render_template("USER/Customize.html"))
    app.add_url_rule('/ai/custom/list', endpoint='custom.page_list', view_func=lambda: render_template("USER/Quiz_list.html"))
    app.add_url_rule('/ai/custom/add',  endpoint='custom.page_add',  view_func=lambda: render_template("USER/Add_Question.html"))
    app.logger.info("[custom] fallback routes mounted: custom.home, custom.page_list, custom.page_add")


# ✅ 再掛 /ai（避免通配路由攔到 /ai/custom/*）
try:
    from ai import bp as ai_bp
    app.register_blueprint(ai_bp, url_prefix="/ai")
except Exception as e:
    app.logger.warning("[ai] disabled: %s", e)


# ===================== 相容路由 =====================
@app.post("/api/register")
def compat_register():
    return _api_register()

@app.post("/api/verify_otp")
def compat_verify_otp():
    return _api_verify_otp()

# ===================== AI 靜態兜底：/ai/uploads/perm/* =====================
AI_UPLOAD_PERM = SERV / "ai" / "uploads" / "perm"
@app.get("/ai/uploads/perm/<path:filename>")
def serve_ai_perm(filename: str):
    target = AI_UPLOAD_PERM / filename
    if not target.exists():
        abort(404)
    return send_from_directory(str(AI_UPLOAD_PERM), filename)

# ===================== 共用設定（避免快取） =====================
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ===================== 首頁 & 靜態 =====================
@app.get("/")
def index():
    return render_template("USER/Index.html")

@app.get("/play")
def legacy_play():
    from flask import redirect, request as _req
    s_type = _req.args.get("s_type", "圖片")
    return redirect(f"/quiz/play?s_type={s_type}", code=302)

@app.get("/USER/<path:filename>")
def user_files(filename: str):
    target = USER_DIR / filename
    if not target.exists():
        abort(404)
    return send_from_directory(str(USER_DIR), filename)

@app.get("/<path:filename>")
def everything(filename: str):
    if filename.startswith("quiz/"):
        abort(404)
    target = SERV_TPL / filename
    if not target.exists():
        abort(404)
    return send_from_directory(str(SERV_TPL), filename)

@app.get("/favicon.ico")
def favicon():
    icon = SERV_STATIC / "favicon.ico"
    if icon.exists():
        return send_from_directory(str(SERV_STATIC), "favicon.ico")
    abort(404)

@app.get("/api/whoami")
def whoami():
    from flask import request
    return jsonify({
        "app_file": __file__,
        "cwd": os.getcwd(),
        "blueprints": sorted(list(app.blueprints.keys())),
        "remote": request.remote_addr,
    })
# ===================== 啟動時檢查 =====================
with app.app_context():
    try:
        from auth import models
        models.PendingVerify.__table__.create(db.engine, checkfirst=True)
    except Exception as e:
        app.logger.error("[auth] create pending_verify failed: %s", e)
    try:
        ensure_default_admin()
    except Exception as e:
        app.logger.error("[auth] ensure_default_admin failed: %s", e)

if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "18361"))
    print("[i] mounted blueprints: /auth , /admin , /quiz , /ai(custom, ai)")
    app.run(host="0.0.0.0", port=port, debug=False)
