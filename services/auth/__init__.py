# services/auth/__init__.py
import os
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_migrate import Migrate

db = SQLAlchemy()
mail = Mail()
migrate = Migrate()

def create_app():
    from werkzeug.middleware.proxy_fix import ProxyFix
    load_dotenv(os.path.join(os.path.dirname(base_dir), "..", ".env"))

    base_dir   = os.path.dirname(os.path.abspath(__file__))
    tpl_dir    = os.path.join(base_dir, "..", "templates")
    static_dir = os.path.join(base_dir, "..", "static")

    app = Flask(__name__, template_folder=tpl_dir, static_folder=static_dir)
    app.secret_key = os.getenv("SECRET_KEY", "dev_key_please_change")

    # DB
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///app.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Mail & Tokens
    app.config.update(
        SERVER_NAME=os.getenv("SERVER_NAME", "140.128.10.26:18360"),
        PREFERRED_URL_SCHEME=os.getenv("PREFERRED_URL_SCHEME","http"),
        MAIL_SERVER=os.getenv("MAIL_SERVER", "localhost"),
        MAIL_PORT=int(os.getenv("MAIL_PORT", "25")),
        MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "False").lower() == "true",
        MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "False").lower() == "true",
        MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
        MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER", "noreply@example.com"),
        DEV_PRINT_VERIFY_LINK=os.getenv("DEV_PRINT_VERIFY_LINK", "True").lower() == "true",
        SECURITY_PASSWORD_SALT=os.getenv("SECURITY_PASSWORD_SALT", "dev_salt"),
        RESET_PASSWORD_SALT=os.getenv("RESET_PASSWORD_SALT", "dev_reset_salt"),
        TOKEN_MAX_AGE=60 * 2,
        RESET_TOKEN_MAX_AGE=60 * 60,
    )

    db.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)

    # ←←← 重要：這裡才匯入 models，避免循環匯入
    from . import models

    # 只嘗試建立 pending_verify 這一張暫存表（不動你現有 DB）
    with app.app_context():
        try:
            models.PendingVerify.__table__.create(db.engine, checkfirst=True)
        except Exception as e:
            app.logger.error(f"Create pending_verify failed: {e}")

    # 註冊藍圖
    from .routes import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix="/auth")

    from .admin_routes import bp as admin_bp, ensure_default_admin
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # 建立預設管理員（如果 DB 沒有）
    with app.app_context():
        try:
            ensure_default_admin()
        except Exception as e:
            app.logger.error(f"ensure_default_admin failed: {e}")

    # 啟動時列個資訊
    with app.app_context():
        from sqlalchemy import inspect
        insp = inspect(db.engine)
        print("[✓] 啟動前 DB 檢查，表：", insp.get_table_names())
        print(f"[i] template_folder = {app.template_folder}")
        print(f"[i] static_folder   = {app.static_folder}")
        print("[i] blueprints mounted: /auth, /admin")

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    return app
