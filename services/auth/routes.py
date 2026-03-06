# services/auth/routes.py
import re
import random
from datetime import datetime, timedelta, timezone
from passlib.hash import bcrypt as passlib_bcrypt

from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    flash, session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from email_validator import validate_email, EmailNotValidError
from flask_mail import Message

from . import db, mail
from .models import User, PendingVerify  # Admin 由 admin_routes 管，這裡不載

bp = Blueprint("auth", __name__, url_prefix="/auth")

# ===================== 工具（token/驗證） =====================
def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key)

def make_token(email: str) -> str:
    return _serializer().dumps(email, salt=current_app.config["SECURITY_PASSWORD_SALT"])

def read_token(token: str, max_age: int | None = None) -> str:
    if max_age is None:
        max_age = current_app.config["TOKEN_MAX_AGE"]
    return _serializer().loads(token, salt=current_app.config["SECURITY_PASSWORD_SALT"], max_age=max_age)

def make_reset_token(email: str) -> str:
    return _serializer().dumps(email, salt=current_app.config["RESET_PASSWORD_SALT"])

def read_reset_token(token: str, max_age: int | None = None) -> str:
    if max_age is None:
        max_age = current_app.config["RESET_TOKEN_MAX_AGE"]
    return _serializer().loads(token, salt=current_app.config["RESET_PASSWORD_SALT"], max_age=max_age)

# 允許兩種生日格式
def _parse_birthday(s: str) -> datetime:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError("birthday format")

PWD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d]{8,12}$")
def is_valid_password(pw: str) -> bool:
    return bool(PWD_REGEX.match(pw or ""))

# ===== 密碼比對（同時支援 pbkdf2/scrypt 與 bcrypt），並提供是否為 bcrypt 的判斷 =====
def smart_check_password(stored_hash: str, plain: str) -> bool:
    if not stored_hash:
        return False
    try:
        if stored_hash.startswith(("pbkdf2:", "scrypt:", "argon2:")):
            return check_password_hash(stored_hash, plain)
        if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
            return passlib_bcrypt.verify(plain, stored_hash)
    except Exception as e:
        current_app.logger.exception("Password verify failed: %s", e)
        return False
    return False

def is_bcrypt_hash(stored_hash: str) -> bool:
    return bool(stored_hash and stored_hash.startswith(("$2a$", "$2b$", "$2y$")))

# ===================== Pages（使用者） =====================
@bp.get("/")
def index():
    if session.get("user_id") or session.get("user"):
        return redirect(url_for("auth.home"))
    return redirect(url_for("auth.login_page"))

@bp.get("/register")
def register_page():
    return render_template("USER/Register.html")

@bp.get("/login")
def login_page():
    from flask import current_app, Response
    import html
    try:
        return render_template("USER/User_Login.html")
    except Exception as e:
        current_app.logger.exception("Login render failed: %s", e)
        return Response(
            f"<h1>Login page render failed</h1><pre>{html.escape(str(e))}</pre>",
            status=500,
            mimetype="text/html"
        )

@bp.get("/forgot")
def forgot_page():
    return render_template("USER/Forgot.html")

# ===================== 註冊：送驗證信 =====================
@bp.post("/api/register")
def api_register():
    data = request.form if request.form else request.json
    name = (data.get("name") or "").strip()
    gender = (data.get("gender") or "").strip().lower()
    birthday = (data.get("birthday") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    avatar = (data.get("avatar") or "").strip()  # 新增：頭像

    # 基本欄位檢查
    if not all([name, gender, birthday, email, password]):
        return jsonify({"ok": False, "msg": "所有欄位皆為必填"}), 400

    # 頭像檢查（必選且必須為 1.png~6.png）
    allowed_avatars = {f"{i}.png" for i in range(1, 7)}
    if not avatar:
        return jsonify({"ok": False, "msg": "請選擇頭像"}), 400
    if avatar not in allowed_avatars:
        return jsonify({"ok": False, "msg": "頭像選擇無效"}), 400
    avatar_path = f"avatars/{avatar}"

    # 其他驗證
    try:
        validate_email(email, allow_smtputf8=True)
    except EmailNotValidError as e:
        return jsonify({"ok": False, "msg": f"Email 無效：{str(e)}"}), 400

    if gender not in ("male", "female", "other"):
        return jsonify({"ok": False, "msg": "性別須為 male/female/other"}), 400

    # 只檢查生日格式是否合規（兩種格式都接受）
    try:
        _ = _parse_birthday(birthday)
    except Exception:
        return jsonify({"ok": False, "msg": "生日格式錯誤，請使用 2000-01-01 或 2000/01/01"}), 400

    if not is_valid_password(password):
        return jsonify({"ok": False, "msg": "密碼需 8~12 碼，包含大小寫字母與數字"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"ok": False, "msg": "此 Email 已完成註冊"}), 400

    # 產生 token / OTP
    token = make_token(email)
    password_hash = generate_password_hash(password)

    now = datetime.utcnow()
    otp = str(random.randint(100000, 999999))
    otp_expire_at = now + timedelta(seconds=120)

    kwargs = dict(
        token=token,
        email=email,
        name=name,
        gender=gender,
        birthday=birthday,
        password_hash=password_hash,
        created_at=datetime.utcnow(),
        otp=otp,
        otp_expire_at=otp_expire_at,
    )
    if hasattr(PendingVerify, "avatar"):
        kwargs["avatar"] = avatar_path

    p = PendingVerify(**kwargs)
    db.session.merge(p)
    db.session.commit()

    if not hasattr(PendingVerify, "avatar"):
        session[f"reg_avatar_{token}"] = avatar_path

    verify_url = url_for("auth.verify_email", token=token, _external=True, _scheme=current_app.config.get("PREFERRED_URL_SCHEME","http"))

    if current_app.config["DEV_PRINT_VERIFY_LINK"]:
        print("\n========== 開發模式：註冊驗證 ==========")
        print("連結：", verify_url)
        print("OTP ：", otp)
        print("頭像：", avatar_path)
        print("======================================\n")
    else:
        subject = "請完成註冊：驗證你的電子信箱"
        body = (
            f"Hi {name}，請在 120 秒內點擊以下連結完成註冊：\n{verify_url}\n\n"
            f"或輸入驗證碼：{otp}（120 秒內有效）"
        )
        try:
            msg = Message(subject=subject, recipients=[email], body=body)
            mail.send(msg)
        except Exception as e:
            PendingVerify.query.filter_by(token=token).delete()
            db.session.commit()
            return jsonify({"ok": False, "msg": f"寄信失敗：{e}"}), 500

    return jsonify({"ok": True, "msg": "驗證信已發送（或已在終端機印出）。", "token": token})

# ===================== 註冊：OTP 驗證 =====================
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy import Date, DateTime

@bp.post("/api/verify_otp")
def api_verify_otp():
    try:
        data = request.form if request.form else (request.json or {})
        token = (data.get("token") or "").strip()
        otp   = (data.get("otp") or "").strip()

        if not token or not otp:
            return jsonify({"ok": False, "msg": "缺少 token 或驗證碼"}), 400

        pending = PendingVerify.query.filter_by(token=token).first()
        if not pending:
            return jsonify({"ok": False, "msg": "驗證資料不存在或已過期"}), 400

        now = datetime.utcnow()
        exp = pending.otp_expire_at
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return jsonify({"ok": False, "msg": "暫存資料時間格式錯誤，請重送驗證"}), 400
        elif hasattr(exp, "tzinfo") and exp.tzinfo is not None:
            exp = exp.replace(tzinfo=None)

        if not pending.otp or not exp or now > exp:
            PendingVerify.query.filter_by(token=token).delete()
            db.session.commit()
            return jsonify({"ok": False, "msg": "驗證碼已過期，請重新發送"}), 400

        if pending.otp != otp:
            return jsonify({"ok": False, "msg": "驗證碼錯誤"}), 400

        try:
            bday_dt = _parse_birthday(pending.birthday)
        except Exception:
            PendingVerify.query.filter_by(token=token).delete()
            db.session.commit()
            return jsonify({"ok": False, "msg": "生日格式錯誤，請重新註冊"}), 400

        birthday_value = bday_dt
        try:
            col = User.__table__.columns.get("birthday")
            if col is not None:
                tp = col.type
                if isinstance(tp, Date) and not isinstance(tp, DateTime):
                    birthday_value = bday_dt.date()
        except Exception:
            birthday_value = bday_dt

        avatar_src = getattr(pending, "avatar", None) if hasattr(PendingVerify, "avatar") else None
        if not avatar_src:
            avatar_src = session.pop(f"reg_avatar_{token}", None)

        if User.query.filter_by(email=pending.email).first():
            PendingVerify.query.filter_by(token=token).delete()
            db.session.commit()
            return jsonify({"ok": True, "msg": "此信箱已完成註冊，請直接登入"}), 200

        now_reg = datetime.utcnow()
        user_kwargs = dict(
            email=pending.email,
            name=pending.name,
            gender=pending.gender,
            birthday=birthday_value,
            password_hash=pending.password_hash,
            is_verified=True,
            register_date=now_reg,
            last_login=now_reg,
            status="active",
            reset_token=""
        )
        if hasattr(User, "avatar"):
            user_kwargs["avatar"] = avatar_src

        user = User(**user_kwargs)
        db.session.add(user)

        PendingVerify.query.filter_by(token=token).delete()
        db.session.commit()

        return jsonify({"ok": True, "msg": "驗證成功，已完成註冊"}), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({"ok": False, "msg": "此 Email 已完成註冊，請直接登入"}), 400
    except DataError:
        db.session.rollback()
        return jsonify({"ok": False, "msg": "資料格式不正確，請檢查生日或欄位長度"}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("api_verify_otp failed: %s", e)
        return jsonify({"ok": False, "msg": "伺服器忙線，請稍後再試（OTP 可重送）"}), 500

# ===================== 註冊：點擊驗證連結 =====================
@bp.get("/verify/<token>")
def verify_email(token):
    try:
        email = read_token(token)
    except SignatureExpired:
        PendingVerify.query.filter_by(token=token).delete()
        db.session.commit()
        flash("驗證連結已過期，請重新註冊。")
        return redirect(url_for("auth.register_page"))
    except BadSignature:
        flash("驗證連結無效。")
        return redirect(url_for("auth.register_page"))

    pending = PendingVerify.query.filter_by(token=token).first()
    if not pending or pending.email != email:
        flash("驗證失敗或連結已使用。")
        return redirect(url_for("auth.register_page"))

    try:
        bday_dt = _parse_birthday(pending.birthday)
    except Exception:
        PendingVerify.query.filter_by(token=token).delete()
        db.session.commit()
        flash("生日格式錯誤，請重新註冊。")
        return redirect(url_for("auth.register_page"))

    now = datetime.utcnow()

    avatar_src = getattr(pending, "avatar", None) if hasattr(PendingVerify, "avatar") else None
    if not avatar_src:
        avatar_src = session.pop(f"reg_avatar_{token}", None)

    user_kwargs = dict(
        email=pending.email,
        name=pending.name,
        gender=pending.gender,
        birthday=bday_dt,
        password_hash=pending.password_hash,
        is_verified=True,
        register_date=now,
        last_login=now,
        status="active",
        reset_token=""
    )
    if hasattr(User, "avatar"):
        user_kwargs["avatar"] = avatar_src

    user = User(**user_kwargs)
    db.session.add(user)
    PendingVerify.query.filter_by(token=token).delete()
    db.session.commit()
    return redirect(url_for("auth.login_page"))

# ===================== 使用者登入 / 登出 =====================
@bp.post("/api/login")
def api_login():
    data = request.form if request.form else request.json
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email, is_verified=True).first()

    # —— 安全比對：支援 pbkdf2/scrypt + bcrypt，且任何例外都不 500 —— 
    ok = False
    if user:
        ok = smart_check_password(getattr(user, "password_hash", ""), password)

    if not ok:
        return jsonify({"ok": False, "msg": "帳號或密碼錯誤"}), 400

    # 若本次命中 bcrypt 舊格式，登入成功即升級為 Werkzeug pbkdf2
    try:
        if is_bcrypt_hash(user.password_hash):
            user.password_hash = generate_password_hash(password)  # pbkdf2:sha256:...
            db.session.commit()
    except Exception as e:
        current_app.logger.warning("Password rehash failed for %s: %s", user.email, e)

    # 帳號狀態
    if getattr(user, "status", "") and str(user.status).lower() != "active":
        return jsonify({"ok": False, "msg": "帳號已停用，請聯絡管理員"}), 403

    # 更新最後登入
    user.last_login = datetime.utcnow()
    db.session.commit()

    uid = getattr(user, "id", None) or getattr(user, "user_id", None)
    session["user_id"] = uid
    session["auth_user_id"] = uid
    session["user"] = user.email
    session.permanent = True

    return jsonify({"ok": True, "msg": "登入成功", "redirect": url_for("auth.home")})

@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ======== 登入保護 ========
from functools import wraps

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        email = session.get("user")
        if not email:
            flash("請先登入")
            return redirect(url_for("auth.login_page"))

        u = User.query.filter_by(email=email, is_verified=True).first()
        if not u:
            session.clear()
            flash("請先登入")
            return redirect(url_for("auth.login_page"))

        if getattr(u, "status", "") and str(u.status).lower() != "active":
            session.clear()
            flash("你的帳號已被停用，請聯絡管理員")
            return redirect(url_for("auth.login_page"))

        return view(*args, **kwargs)
    return wrapper

# ======== 個人檔案頁 ========
@bp.get("/profile")
@login_required
def profile_page():
    return render_template("USER/Profile.html")

# ======== 讀取目前使用者資料 ========
@bp.get("/api/me")
@login_required
def api_me():
    email = session["user"]
    u = User.query.filter_by(email=email, is_verified=True).first()
    if not u:
        return jsonify({"ok": False, "msg": "找不到使用者"}), 404

    bday = u.birthday.date().isoformat() if hasattr(u.birthday, "date") else u.birthday.strftime("%Y-%m-%d")
    avatar_value = getattr(u, "avatar", None) if hasattr(u, "avatar") else None

    return jsonify({
        "ok": True,
        "data": {
            "name": u.name,
            "gender": u.gender,
            "birthday": bday,
            "email": u.email,
            "avatar": avatar_value
        }
    })

# ======== 更新一般欄位 ========
@bp.post("/api/me")
@login_required
def api_update_me():
    email = session["user"]
    u = User.query.filter_by(email=email, is_verified=True).first()
    if not u:
        return jsonify({"ok": False, "msg": "找不到使用者"}), 404

    data = request.form if request.form else request.json or {}

    new_name = (data.get("name") or u.name).strip()
    new_gender = (data.get("gender") or u.gender).strip().lower()
    new_birthday = (data.get("birthday") or None)

    incoming_email = (data.get("email") or u.email).strip().lower()
    if incoming_email != u.email:
        return jsonify({"ok": False, "msg": "Email 不可變更"}), 400

    if new_gender not in ("male", "female", "other"):
        return jsonify({"ok": False, "msg": "性別須為 male/female/other"}), 400

    try:
        bday_dt = _parse_birthday(new_birthday) if new_birthday else u.birthday
    except Exception:
        return jsonify({"ok": False, "msg": "生日格式錯誤，請用 2000-01-01 或 2000/01/01"}), 400

    u.name = new_name
    u.gender = new_gender
    u.birthday = bday_dt
    db.session.commit()

    return jsonify({"ok": True, "msg": "資料已更新"})

# ======== 修改密碼（需提供舊密碼） ========
@bp.post("/api/me/password")
@login_required
def api_me_password():
    email = session["user"]
    u = User.query.filter_by(email=email, is_verified=True).first()
    if not u:
        return jsonify({"ok": False, "msg": "找不到使用者"}), 404

    data = request.form if request.form else request.json or {}
    old_pw = data.get("old_password") or ""
    new_pw = data.get("new_password") or ""
    confirm_pw = data.get("confirm_password") or ""

    # 舊密碼驗證也用 smart_check_password，避免 bcrypt 造成 500
    if not smart_check_password(u.password_hash, old_pw):
        return jsonify({"ok": False, "msg": "舊密碼錯誤"}), 400
    if new_pw != confirm_pw:
        return jsonify({"ok": False, "msg": "兩次輸入的密碼不一致"}), 400
    if not is_valid_password(new_pw):
        return jsonify({"ok": False, "msg": "密碼需 8~12 碼，且包含大小寫字母與數字"}), 400

    u.password_hash = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({"ok": True, "msg": "密碼已更新"})

# ===================== 忘記密碼 =====================
@bp.post("/api/forgot")
def api_forgot():
    data = request.form if request.form else request.json
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "msg": "請輸入電子信箱"}), 400

    user = User.query.filter_by(email=email, is_verified=True).first()
    generic_msg = "若此信箱存在於系統，已寄出重設密碼連結（請查收，含垃圾信匣）。"
    if not user:
        return jsonify({"ok": True, "msg": generic_msg})

    token = make_reset_token(email)
    reset_url = url_for(
        "auth.reset_password_page",
        token=token,
        _external=True,
        _scheme=current_app.config.get("PREFERRED_URL_SCHEME","http")
    )

    if current_app.config["DEV_PRINT_VERIFY_LINK"]:
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

# ===================== 重設密碼：由信件中的連結進入頁面 =====================
@bp.get("/reset/<token>")
def reset_password_page(token):
    try:
        email = read_reset_token(token)
    except SignatureExpired:
        flash("重設連結已過期，請重新申請。")
        return redirect(url_for("auth.forgot_page"))
    except BadSignature:
        flash("重設連結無效。")
        return redirect(url_for("auth.forgot_page"))

    return render_template("USER/Forgot.html", token=token, email=email)

@bp.post("/api/reset")
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

# ===================== 測試寄信 =====================
@bp.get("/test_mail")
def test_mail():
    to = request.args.get("to") or current_app.config.get("MAIL_USERNAME")
    try:
        msg = Message(subject="[測試] StarStar 寄信測試", recipients=[to], body="這是一封測試信件。")
        mail.send(msg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "msg": f"已嘗試寄信到 {to}，請檢查收件匣/垃圾信"}), 200

# ====== 取代原本的 /home ======
@bp.get("/home")
@login_required
def home():
    from flask import Response
    import html
    try:
        return render_template("USER/User_Home.html")
    except Exception as e:
        current_app.logger.exception("/auth/home render failed: %s", e)
        # ⚠️ 只在除錯期間使用；定位完要改回正常版以免外洩細節
        return Response(
            f"<h1>/auth/home render failed</h1><pre>{html.escape(str(e))}</pre>",
            status=500,
            mimetype="text/html"
        )
