# services/auth/auth.py
import os
import re
import random
from datetime import datetime, timezone, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask_mail import Mail, Message
from email_validator import validate_email, EmailNotValidError
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from auth.routes import api_verify_otp as _auth_api_verify_otp


# ===================== 路徑設定（指向 services/templates 與 services/static） =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # .../services/auth
TPL_DIR = os.path.join(BASE_DIR, "..", "templates")     # .../services/templates
STATIC_DIR = os.path.join(BASE_DIR, "..", "static")     # .../services/static

# ✅ 只保留這一個 Flask 實例，指定正確的 template/static 目錄
app = Flask(__name__, template_folder=TPL_DIR, static_folder=STATIC_DIR)

# ===================== 基本設定 =====================
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), ".env"))
app.secret_key = os.getenv("SECRET_KEY", "dev_key_please_change")

# itsdangerous（註冊驗證）
serializer = URLSafeTimedSerializer(app.secret_key)
SECURITY_PASSWORD_SALT = os.getenv("SECURITY_PASSWORD_SALT", "dev_salt")
TOKEN_MAX_AGE = 60 * 2  # 註冊驗證連結120秒有效

# itsdangerous（重設密碼）
RESET_PASSWORD_SALT = os.getenv("RESET_PASSWORD_SALT", "dev_reset_salt")
RESET_TOKEN_MAX_AGE = 60 * 60  # 重設密碼連結 1 小時有效

def make_token(email: str) -> str:
    return serializer.dumps(email, salt=SECURITY_PASSWORD_SALT)

def read_token(token: str, max_age: int = TOKEN_MAX_AGE) -> str:
    return serializer.loads(token, salt=SECURITY_PASSWORD_SALT, max_age=max_age)

def make_reset_token(email: str) -> str:
    return serializer.dumps(email, salt=RESET_PASSWORD_SALT)

def read_reset_token(token: str, max_age: int = RESET_TOKEN_MAX_AGE) -> str:
    return serializer.loads(token, salt=RESET_PASSWORD_SALT, max_age=max_age)

# Mail（可在 .env 切換 DEV_PRINT_VERIFY_LINK）
app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER", "localhost"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", "25")),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "False").lower() == "true",
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "False").lower() == "true",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER", "noreply@example.com"),
)


# ===================== DB (MariaDB) =====================
db_uri = os.getenv("DATABASE_URL")
if not db_uri:
    print("[⚠️] DATABASE_URL not found, using fallback sqlite:///app.db")
    db_uri = "sqlite:///app.db"
else:
    print(f"[✅] Using DATABASE_URL = {db_uri}")

app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ===================== 密碼規則 =====================
PWD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d]{8,12}$")
def is_valid_password(pw: str) -> bool:
    return bool(PWD_REGEX.match(pw or ""))

# ===================== Pages =====================
@app.get("/")
@app.get("/auth")
def index():
    user = session.get("user")
    return render_template("USER/Index.html", user=user)

@app.get("/login")
def login_page():
    return render_template("USER/User_Login.html")

@app.get("/register")
def register_page():
    return render_template("USER/Register.html")

@app.get("/forgot")
def forgot_page():
    return render_template("USER/Forgot.html")  # 如果還沒有 Forgot.html，先在 templates/USER/ 裡建立一個簡單頁面
@app.post("/api/verify_otp")
def _compat_verify_otp():
    # 直接呼叫藍圖裡的處理函式，參數由 Flask request 共用
    return _auth_api_verify_otp()

# ===================== 註冊：送驗證信 =====================
@app.post("/api/register")
def api_register():
    data = request.form if request.form else request.json
    name = (data.get("name") or "").strip()
    gender = (data.get("gender") or "").strip().lower()
    birthday = (data.get("birthday") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not all([name, gender, birthday, email, password]):
        return jsonify({"ok": False, "msg": "所有欄位皆為必填"}), 400

    try:
        validate_email(email, allow_smtputf8=True)
    except EmailNotValidError as e:
        return jsonify({"ok": False, "msg": f"Email 無效：{str(e)}"}), 400

    if gender not in ("male", "female", "other"):
        return jsonify({"ok": False, "msg": "性別須為 male/female/other"}), 400

    try:
        datetime.strptime(birthday, "%Y-%m-%d")
    except Exception:
        return jsonify({"ok": False, "msg": "生日格式需為 YYYY-MM-DD"}), 400

    if not is_valid_password(password):
        return jsonify({"ok": False, "msg": "密碼需 8~12 碼，包含大小寫字母與數字"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"ok": False, "msg": "此 Email 已完成註冊"}), 400

    # 暫存資料 & 產驗證連結（可同時提供 OTP）
    token = make_token(email)
    password_hash = generate_password_hash(password)
    otp = str(random.randint(100000, 999999))
    otp_expire_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    p = PendingVerify(
        token=token, email=email, name=name, gender=gender, birthday=birthday,
        password_hash=password_hash, created_at=datetime.now(timezone.utc),
        otp=otp, otp_expire_at=otp_expire_at
    )
    db.session.merge(p)
    db.session.commit()

    verify_url = url_for("verify_email", token=token, _external=True)

    if DEV_PRINT_VERIFY_LINK:
        print("\n========== 開發模式：註冊驗證 ==========")
        print("連結：", verify_url)
        print("OTP ：", otp)
        print("======================================\n")
    else:
        subject = "請完成註冊：驗證你的電子信箱"
        body = (
            f"Hi {name}，請在 24 小時內點擊以下連結完成註冊：\n{verify_url}\n\n"
            f"或輸入驗證碼：{otp}（10 分鐘內有效）"
        )
        try:
            msg = Message(subject=subject, recipients=[email], body=body)
            mail.send(msg)
        except Exception as e:
            PendingVerify.query.filter_by(token=token).delete()
            db.session.commit()
            return jsonify({"ok": False, "msg": f"寄信失敗：{e}"}), 500

    return jsonify({"ok": True, "msg": "驗證信已發送（或已在終端機印出）。", "token": token})

# ===================== 註冊：點擊驗證 =====================
@app.get("/verify/<token>")
def verify_email(token):
    try:
        email = read_token(token)
    except SignatureExpired:
        PendingVerify.query.filter_by(token=token).delete()
        db.session.commit()
        flash("驗證連結已過期，請重新註冊。")
        return redirect(url_for("register_page"))
    except BadSignature:
        flash("驗證連結無效。")
        return redirect(url_for("register_page"))

    pending = PendingVerify.query.filter_by(token=token).first()
    if not pending or pending.email != email:
        flash("驗證失敗或連結已使用。")
        return redirect(url_for("register_page"))

    user = User(
        email=pending.email, name=pending.name, gender=pending.gender,
        birthday=pending.birthday, password_hash=pending.password_hash,
        is_verified=True, created_at=datetime.now(timezone.utc)
    )
    db.session.add(user)
    PendingVerify.query.filter_by(token=token).delete()
    db.session.commit()
    return render_template("verify_success.html", email=user.email)

# ===================== 登入 / 登出 =====================
@app.post("/api/login")
def api_login():
    data = request.form if request.form else request.json
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email, is_verified=True).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "msg": "帳號或密碼錯誤"}), 400

    uid = getattr(user, "id", None) or getattr(user, "user_id", None)
    session["user_id"] = uid
    session["auth_user_id"] = uid
    session["user"] = user.email
    session.permanent = True

@app.get("/logout")
def logout():
    session.clear()
    flash("你已登出")
    return redirect(url_for("index"))

# ===================== 忘記密碼：申請重設 =====================
@app.post("/api/forgot")
def api_forgot():
    data = request.form if request.form else request.json
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "msg": "請輸入電子信箱"}), 400

    user = User.query.filter_by(email=email, is_verified=True).first()

    # 統一回覆，避免帳號被探測
    generic_msg = "若此信箱存在於系統，已寄出重設密碼連結（請查收，含垃圾信匣）。"

    if not user:
        return jsonify({"ok": True, "msg": generic_msg})

    token = make_reset_token(email)
    reset_url = url_for("reset_password_page", token=token, _external=True)

    if DEV_PRINT_VERIFY_LINK:
        print("\n========== 開發模式：重設密碼 ==========")
        print("重設連結：", reset_url)
        print("======================================\n")
        return jsonify({"ok": True, "msg": generic_msg})

    subject = "重設密碼通知"
    body = (
        f"Hi {user.name}：\n我們收到了重設密碼請求。\n"
        f"請在 1 小時內點擊以下連結重設密碼：\n{reset_url}\n\n"
        f"若非你本人操作，請忽略此信。"
    )
    try:
        msg = Message(subject=subject, recipients=[email], body=body)
        mail.send(msg)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"寄信失敗：{e}"}), 500

    return jsonify({"ok": True, "msg": generic_msg})

@app.get("/reset/<token>")
def reset_password_page(token):
    try:
        email = read_reset_token(token)
    except SignatureExpired:
        flash("重設連結已過期，請重新申請。")
        return redirect(url_for("forgot_page"))
    except BadSignature:
        flash("重設連結無效。")
        return redirect(url_for("forgot_page"))

    return render_template("reset.html", token=token, email=email)

@app.post("/api/reset")
def api_reset_password():
    data = request.form if request.form else request.json
    token = (data.get("token") or "").strip()
    new_pw = data.get("new_password") or ""
    confirm_pw = data.get("confirm_password") or ""

    if not token:
        return jsonify({"ok": False, "msg": "重設連結無效"}), 400

    try:
        email = read_reset_token(token)
    except SignatureExpired:
        return jsonify({"ok": False, "msg": "重設連結已過期，請重新申請"}), 400
    except BadSignature:
        return jsonify({"ok": False, "msg": "重設連結無效"}), 400

    user = User.query.filter_by(email=email, is_verified=True).first()
    if not user:
        return jsonify({"ok": False, "msg": "帳號不存在或未驗證"}), 400

    if new_pw != confirm_pw:
        return jsonify({"ok": False, "msg": "兩次輸入的密碼不一致"}), 400
    if not is_valid_password(new_pw):
        return jsonify({"ok": False, "msg": "密碼需 8~12 碼，且包含大小寫字母與數字"}), 400

    user.password_hash = generate_password_hash(new_pw)
    db.session.commit()

    return jsonify({"ok": True, "msg": "密碼已更新，請使用新密碼登入"})

# =====================（可選）OTP 驗證 API =====================
@app.post("/api/verify_otp")
def api_verify_otp():
    data = request.form if request.form else request.json
    token = (data.get("token") or "").strip()
    otp = (data.get("otp") or "").strip()
    pending = PendingVerify.query.filter_by(token=token).first()
    if not pending:
        return jsonify({"ok": False, "msg": "驗證資料不存在或已過期"}), 400
    if pending.otp != otp:
        return jsonify({"ok": False, "msg": "驗證碼錯誤"}), 400
    if datetime.now(timezone.utc) > pending.otp_expire_at:
        PendingVerify.query.filter_by(token=token).delete()
        db.session.commit()
        return jsonify({"ok": False, "msg": "驗證碼已過期"}), 400

    user = User(
        email=pending.email, name=pending.name, gender=pending.gender,
        birthday=pending.birthday, password_hash=pending.password_hash,
        is_verified=True, created_at=datetime.now(timezone.utc)
    )
    db.session.add(user)
    PendingVerify.query.filter_by(token=token).delete()
    db.session.commit()
    return jsonify({"ok": True, "msg": "驗證成功，已完成註冊"})



# 放在 app.run 之前！
@app.before_request
def enforce_parent_timer():
    path = (request.path or "")

    allow_prefix = (
        "/static/",
        "/auth/api/",
        "/api/",
    )
    allow_exact = {
        "/", "/auth",
        "/login", "/logout",
        "/register", "/forgot",
        "/auth/login", "/auth/logout", "/auth/register", "/auth/forgot",
        "/timer/start", "/timer/stop",
    }

    if path in allow_exact or any(path.startswith(p) for p in allow_prefix):
        return None

    end_ms = session.get("timer_end_ms")
    if end_ms and end_ms <= int(datetime.now(timezone.utc).timestamp() * 1000):
        session.pop("timer_end_ms", None)
        # 先用 blueprint 名稱，如果不存在就用裸路由（雙保險）
        logout_ep = "auth.logout" if "auth.logout" in app.view_functions else "logout"
        return redirect(url_for(logout_ep))


@app.post("/timer/start")
def api_timer_start():
    data = request.get_json(silent=True) or {}
    end_at_ms = int(data.get("end_at_ms", 0))
    if end_at_ms <= 0:
        return jsonify({"ok": False, "msg": "bad end"}), 400
    session["timer_end_ms"] = end_at_ms
    session.modified = True
    return jsonify({"ok": True})


@app.post("/timer/stop")
def api_timer_stop():
    session.pop("timer_end_ms", None)
    session.modified = True
    return jsonify({"ok": True})

# ===================== 啟動（自動建表＋列出路由） =====================
if __name__ == "__main__":
    from sqlalchemy import inspect
    with app.app_context():
        db.create_all()
        insp = inspect(db.engine)
        print("[✓] 啟動前 DB 檢查，表：", insp.get_table_names())
        print("[✓] 路由清單：")
        for r in app.url_map.iter_rules():
            print(" ", r.endpoint, "->", r.rule)
        # 額外印出實際使用的 templates/static 路徑，方便你確認
        print("[i] template_folder =", app.template_folder)
        print("[i] static_folder   =", app.static_folder)

    # 固定用 5058（同學各自分工、用 curl 測試）
    app.run(host="127.0.0.1", port=5058, debug=True)
