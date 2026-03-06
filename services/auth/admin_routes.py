# services/auth/admin_routes.py
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, jsonify, flash, current_app
)
from services.ai.db_config import get_conn
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import TemplateNotFound
from . import db
from .models import Admin, User
from sqlalchemy import text
bp = Blueprint("admin", __name__)
# ===============================
# ⭐⭐ 修正：加上 url_prefix="/admin"
# ===============================
bp = Blueprint("admin", __name__, url_prefix="/admin")

# =========================
# 台北時區轉換（ZoneInfo 有就用；沒有就用 pytz）
# =========================
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    _tz_utc = ZoneInfo("UTC")
    _tz_tpe = ZoneInfo("Asia/Taipei")
    def _to_tz(dt: datetime, tz_target) -> datetime:
        if dt is None:
            return None
        # aware → 先轉 UTC，再轉目標
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(_tz_utc)
        else:
            # naive → 視為 UTC
            dt_utc = dt.replace(tzinfo=_tz_utc)
        return dt_utc.astimezone(tz_target)
except Exception:
    # fallback: pytz
    import pytz
    _tz_utc = pytz.utc
    _tz_tpe = pytz.timezone("Asia/Taipei")
    def _to_tz(dt: datetime, tz_target) -> datetime:
        if dt is None:
            return None
        if dt.tzinfo is not None:
            # 已 aware：先轉 UTC，再轉目標
            dt_utc = dt.astimezone(_tz_utc)
        else:
            # naive → 視為 UTC
            dt_utc = dt.replace(tzinfo=_tz_utc)
        return dt_utc.astimezone(tz_target)

def to_taipei(dt: datetime) -> datetime | None:
    return _to_tz(dt, _tz_tpe)

def fmt_dt_local(dt: datetime) -> str:
    """輸出為 'YYYY/MM/DD HH:MM'（台北時間）。若時間是 00:00 則省略時間。"""
    try:
        local = to_taipei(dt)
        if not local:
            return ""
        s = local.strftime("%Y/%m/%d %H:%M")
        return s.replace(" 00:00", "")
    except Exception:
        return ""

# =========================
# 管理員登入驗證等
# =========================

def ensure_default_admin():
    username = os.getenv("ADMIN_USERNAME", "admin")
    pwd = os.getenv("ADMIN_PASSWORD", "Admin1234")
    if not Admin.query.filter_by(username=username).first():
        a = Admin(username=username, password_hash=generate_password_hash(pwd))
        db.session.add(a)
        db.session.commit()
        print(f"[admin] 預設管理員已建立：{username}")

def admin_required(view):
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            flash("請先登入管理員")
            return redirect(url_for("admin.login_page"))
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper

def admin_required_api(view):
    """API 專用：未登入回傳 JSON 401"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"ok": False, "msg": "未登入管理員"}), 401
        return view(*args, **kwargs)
    return wrapper

# =========================
# 頭像路徑處理
# =========================
def build_avatar_url(raw: str | None):
    """
    把 DB 的 avatar 欄位正規化成可用的 URL。
    支援：
      - http/https/data: 直接用
      - /static/... 直接用
      - static/...、avatars/... 自動補上 /static/
    """
    if not raw:
        return None
    v = str(raw).strip()

    if v.startswith(("http://", "https://", "data:")):
        return v
    if v.startswith("/static/"):
        return v

    v = v.lstrip("./")
    if v.startswith("static/static/"):
        v = v[len("static/"):]
    if v.startswith("static/"):
        v = v[len("static/"):]

    return url_for("static", filename=v)


@bp.get("/ai/metrics")
@admin_required
def ai_metrics_page():
    """
    後台『AI 品質提升｜數據看板』頁面
    對應模板：services/templates/ADMIN/ai-metrics.html
    """
    return render_template("ADMIN/ai-metrics.html")

@bp.get("/api/ai/metrics/summary")
@admin_required_api
def api_ai_metrics_summary():
    conn = None
    cur = None

    # --- helpers: 將 fetch 結果統一轉成 dict ---
    def _fetch_all_dict(c):
        rows = c.fetchall() or []
        if not rows:
            return []
        if isinstance(rows[0], dict):
            return rows
        cols = [d[0] for d in c.description]
        return [{cols[i]: r[i] for i in range(len(cols))} for r in rows]

    def _fetch_one_dict(c):
        row = c.fetchone()
        if not row:
            return {}
        if isinstance(row, dict):
            return row
        cols = [d[0] for d in c.description]
        return {cols[i]: row[i] for i in range(len(cols))}

    try:
        conn = get_conn()

        # 儘量用 DictCursor；不行就退回一般 cursor
        try:
            import pymysql
            cur = conn.cursor(pymysql.cursors.DictCursor)  # PyMySQL
        except Exception:
            try:
                import MySQLdb.cursors as mdbc
                cur = conn.cursor(mdbc.DictCursor)          # mysqlclient
            except Exception:
                cur = conn.cursor()                         # fallback: tuple rows

        # ---- A) Calibration / Rules 版本（改成讀 AppConfig）----
        active_calib_id, active_rules_id = None, None
        try:
            cur.execute("""
                SELECT cfg_key, cfg_val
                FROM AppConfig
                WHERE cfg_key IN ('calibration_version','rules_version')
            """)
            rows = _fetch_all_dict(cur)
            kv = {r['cfg_key']: r['cfg_val'] for r in rows}
            active_calib_id = kv.get('calibration_version')
            active_rules_id = kv.get('rules_version')
        except Exception as e:
            current_app.logger.warning(f"AppConfig miss or error: {e}")

        # ---- 1) 批次趨勢 ----
        # ---- 1) 批次趨勢 ----
        batch_trend = []
        recent_acc_pct = 0.0
        try:
            cur.execute("""
                SELECT batch_label AS week, acc_pct
                FROM v_ai_batch_trend
                ORDER BY week DESC
                LIMIT 12
            """)
            rows = _fetch_all_dict(cur)
            # 確保 acc_pct 是 float
            for r in rows:
                try:
                    r["acc_pct"] = float(r.get("acc_pct") or 0)
                except Exception:
                    r["acc_pct"] = 0.0
            batch_trend = list(reversed(rows))
            last3 = batch_trend[-3:] if len(batch_trend) >= 3 else batch_trend
            if last3:
                recent_acc_pct = round(sum(r["acc_pct"] for r in last3) / len(last3), 2)
        except Exception as e:
            current_app.logger.warning(f"batch_trend view missing or error: {e}")

        # ---- 2) 各情緒準確率 ----
        emo_acc = []
        try:
            cur.execute("""
                SELECT emotion, ROUND(AVG(acc_pct),2) AS acc_pct
                FROM v_ai_emotion_accuracy
                GROUP BY emotion
                ORDER BY emotion
            """)
            rows = _fetch_all_dict(cur)
            # 也轉 float
            emo_acc = []
            for r in rows:
                v = r.get("acc_pct")
                try:
                    v = float(v or 0)
                except Exception:
                    v = 0.0
                emo_acc.append({"emotion": r.get("emotion") or "", "acc_pct": v})
        except Exception as e:
            current_app.logger.warning(f"emo_acc view missing or error: {e}")


        # ---- 3) Top 混淆對（如果沒有該 view 會抓不到，讓它安靜失敗即可）----
        top_confusions = []
        try:
            cur.execute("""
                SELECT pair, cnt
                FROM v_ai_top_confusions
                ORDER BY cnt DESC
                LIMIT 5
            """)
            top_confusions = _fetch_all_dict(cur)
        except Exception as e:
            current_app.logger.warning(f"top_confusions view missing or error: {e}")

        # ---- 4) 近期拒判率 ----
        recent_reject_pct = 0.0
        try:
            cur.execute("""
                SELECT ROUND(SUM(reject_cnt)/SUM(total_cnt)*100,2) AS reject_rate
                FROM v_ai_recent_reject_rate
            """)
            row = _fetch_one_dict(cur)
            v = row.get("reject_rate")
            if v is not None:
                recent_reject_pct = float(v)
        except Exception as e:
            current_app.logger.warning(f"recent_reject view error: {e}")

        if recent_reject_pct == 0.0:
            # fallback：直接掃 AIUploadItem 最近 14 天
            try:
                cur.execute("""
                    SELECT
                      SUM(CASE WHEN state='rejected' THEN 1 ELSE 0 END) AS reject_cnt,
                      COUNT(*) AS total_cnt
                    FROM AIUploadItem
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
                """)
                row = _fetch_one_dict(cur)
                rc = float(row.get("reject_cnt") or 0)
                tc = float(row.get("total_cnt") or 0)
                recent_reject_pct = round((rc / tc * 100.0), 2) if tc > 0 else 0.0
            except Exception as e:
                current_app.logger.warning(f"recent_reject fallback error: {e}")

        # ---- 統一回傳 ----
        return jsonify({
            "active_calib_id": active_calib_id,
            "active_rules_id": active_rules_id,
            "recent_acc_pct": recent_acc_pct,
            "recent_reject_pct": recent_reject_pct,
            "batch_trend": batch_trend,
            "emo_acc": emo_acc,
            "top_confusions": top_confusions
        })

    except Exception as e:
        current_app.logger.exception("metrics summary failed")
        return jsonify({
            "active_calib_id": None,
            "active_rules_id": None,
            "recent_acc_pct": 0.0,
            "recent_reject_pct": 0.0,
            "batch_trend": [],
            "emo_acc": [],
            "top_confusions": []
        }), 200
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass



# =========================
# 頁面與 API
# =========================
@bp.get("/")
def index():
    return redirect(url_for("admin.login_page"))

@bp.get("/login")
def login_page():
    return render_template("ADMIN/login.html")
@bp.get("/add-user", endpoint="add_user")
@admin_required
def add_user_page():
    # 這裡先顯示對應模板；之後要接功能再填
    return render_template("ADMIN/add-user.html")
@bp.post("/api/login")
def api_login():
    data = request.form if request.form else request.json
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    admin = Admin.query.filter_by(username=username).first()
    if not admin or not check_password_hash(admin.password_hash, password):
        return jsonify({"ok": False, "msg": "帳號或密碼錯誤"}), 400

    session["admin"] = admin.username
    return jsonify({"ok": True, "msg": "登入成功", "redirect": url_for("admin.frontpage")})

@bp.get("/frontpage")
@admin_required
def frontpage():
    try:
        return render_template("ADMIN/frontpage.html")
    except TemplateNotFound as e:
        current_app.logger.error(f"Template not found: {e}")
        try:
            templates = sorted(current_app.jinja_env.list_templates())
        except Exception:
            templates = []
        msg = [
            "找不到模板：ADMIN/frontpage.html",
            "請確認檔案路徑：services/templates/ADMIN/frontpage.html（大小寫要一致）",
            f"目前 template_folder = {current_app.template_folder}",
            "目前可見模板清單（前 50 筆）：",
            "\n".join(templates[:50]) or "(空)"
        ]
        return "<pre>" + "\n".join(msg) + "</pre>", 500
    except Exception as e:
        current_app.logger.exception("Render frontpage 發生未預期錯誤")
        return (
            "<pre>frontpage 渲染發生錯誤：\n"
            f"{e}\n\n"
            "請檢查模板內是否有指向不存在的路由，例如 url_for('admin.xxx')。</pre>"
        ), 500

# 使用者資料頁
@bp.get("/users", endpoint="user_information")
@admin_required
def user_information():
    try:
        return render_template("ADMIN/Userinformation.html")
    except TemplateNotFound:
        return (
            "<h1 style='font-family:sans-serif'>使用者資料</h1>"
            "<p>你尚未建立 <code>services/templates/ADMIN/user_information.html</code>，先顯示預設占位頁。</p>",
            200
        )

# services/auth/admin_routes.py 內的 api_overview()

@bp.get("/api/overview")
def api_overview():
    """
    回傳：總題目數、總用戶、整體平均正確率，以及三類題庫的總題數與平均正確率
    """
    sqls = {
        # 換成抓 max_question_id
        "total": text("SELECT max_question_id FROM v_total_questions LIMIT 1"),

        # 這兩行維持原本就對的
        "users": text("SELECT total_users FROM v_total_users LIMIT 1"),
        "acc_all": text("SELECT avg_accuracy_pct FROM v_accuracy_overall LIMIT 1"),

        "img_cnt": text("SELECT total_image_questions FROM v_total_image_questions LIMIT 1"),
        "img_acc": text("SELECT image_accuracy FROM v_acc_image LIMIT 1"),

        "gif_cnt": text("SELECT total_gif_questions FROM v_total_gif_questions LIMIT 1"),
        "gif_acc": text("SELECT gif_accuracy FROM v_acc_gif LIMIT 1"),

        "scn_cnt": text("SELECT total_scene_questions FROM v_total_scene_questions LIMIT 1"),
        "scn_acc": text("SELECT scene_accuracy FROM v_acc_scene LIMIT 1"),
    }

    def _one(sql):
        row = db.session.execute(sql).first()
        return 0 if row is None else (row[0] or 0)

    data = {
        "total_questions": _one(sqls["total"]),     # 這裡仍用 total_questions 當回應 key，不用改前端
        "total_users":     _one(sqls["users"]),
        "avg_accuracy_pct": float(_one(sqls["acc_all"])),

        "image": {"count": _one(sqls["img_cnt"]), "accuracy": float(_one(sqls["img_acc"]))},
        "gif":   {"count": _one(sqls["gif_cnt"]), "accuracy":  float(_one(sqls["gif_acc"]))},
        "scene": {"count": _one(sqls["scn_cnt"]), "accuracy": float(_one(sqls["scn_acc"]))},
    }
    return jsonify(data)
@bp.get("/api/weekly/answer-rate")
def api_week_answer_rate():
    """
    指定偏移週每日回合正確率（%），不做任何時區轉換。
    Query: ?offset=0  (0=本週, -1=上週, -2=上上週...)
    回傳：[{day:"YYYY-MM-DD", rate_pct: 小數}]
    """
    from sqlalchemy import text as _t
    try:
        offset = int(request.args.get("offset", "0"))
    except Exception:
        offset = 0

    # 以本機時間計算週區間（週一 00:00 ~ 下週一 00:00）
    now_local = datetime.now()
    monday_local = now_local - timedelta(days=now_local.weekday())  # Monday=0
    monday_local = monday_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = monday_local + timedelta(days=7*offset)
    end_local   = start_local + timedelta(days=7)

    # 動態 SQL（不轉時區）
    q = _t("""
        WITH RECURSIVE days(n, day) AS (
          SELECT 0, DATE(:start_local)
          UNION ALL
          SELECT n+1, DATE(DATE_ADD(:start_local, INTERVAL n+1 DAY))
          FROM days WHERE n < 6
        ),
        per AS (
          SELECT
            DATE(ss.start_time) AS day,
            SUM(CASE
                  WHEN sr.user_answer IS NOT NULL
                   AND TRIM(sr.user_answer) <> ''
                   AND TRIM(sr.user_answer) <> '未作答'
                THEN 1 ELSE 0 END) AS answered_cnt,
            SUM(CASE
                  WHEN sr.user_answer IS NOT NULL
                   AND TRIM(sr.user_answer) <> ''
                   AND TRIM(sr.user_answer) <> '未作答'
                   AND sr.is_correct = 1
                THEN 1 ELSE 0 END) AS correct_cnt
          FROM SystemSession ss
          JOIN SystemRecord  sr ON sr.ss_id = ss.ss_id
          WHERE ss.start_time >= :start_local AND ss.start_time < :end_local
          GROUP BY day
        )
        SELECT
          d.day,
          ROUND(100 * COALESCE(p.correct_cnt,0) / NULLIF(p.answered_cnt,0), 1) AS rate_pct
        FROM days d
        LEFT JOIN per p ON p.day = d.day
        ORDER BY d.day
    """)

    rows = db.session.execute(q, {
        "start_local": start_local.strftime("%Y-%m-%d %H:%M:%S"),
        "end_local":   end_local.strftime("%Y-%m-%d %H:%M:%S"),
    }).fetchall()

    return jsonify([{"day": day.isoformat(), "rate_pct": float(rate or 0.0)} for day, rate in rows])
@bp.get("/api/weekly/signups")
def api_week_signups():
    """
    指定偏移週每日註冊數，不做任何時區轉換。
    Query: ?offset=0  (0=本週, -1=上週, ...)
    回傳：[{day:"YYYY-MM-DD", signups: 整數}]
    """
    from sqlalchemy import text as _t
    try:
        offset = int(request.args.get("offset", "0"))
    except Exception:
        offset = 0

    now_local = datetime.now()
    monday_local = now_local - timedelta(days=now_local.weekday())
    monday_local = monday_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = monday_local + timedelta(days=7*offset)
    end_local   = start_local + timedelta(days=7)

    q = _t("""
        WITH RECURSIVE days(n, day) AS (
          SELECT 0, DATE(:start_local)
          UNION ALL
          SELECT n+1, DATE(DATE_ADD(:start_local, INTERVAL n+1 DAY)) FROM days WHERE n < 6
        ),
        per AS (
          SELECT
            DATE(u.register_date) AS day,
            COUNT(*) AS signups
          FROM `User` u
          WHERE u.register_date >= :start_local
            AND u.register_date <  :end_local
          GROUP BY day
        )
        SELECT d.day, COALESCE(p.signups,0) AS signups
        FROM days d
        LEFT JOIN per p ON p.day = d.day
        ORDER BY d.day
    """)

    rows = db.session.execute(q, {
        "start_local": start_local.strftime("%Y-%m-%d %H:%M:%S"),
        "end_local":   end_local.strftime("%Y-%m-%d %H:%M:%S"),
    }).fetchall()

    return jsonify([{"day": day.isoformat(), "signups": int(cnt or 0)} for day, cnt in rows])


@bp.get("/api/users", endpoint="api_users")
@admin_required_api
def api_users():
    q = User.query
    if hasattr(User, "is_verified"):
        q = q.filter_by(is_verified=True)
    if hasattr(User, "register_date"):
        q = q.order_by(User.register_date.desc())

    users = q.all()
    data = []
    for u in users:
        data.append({
            "user_id": getattr(u, "user_id", None),
            "name": getattr(u, "name", "") or "",
            "email": getattr(u, "email", "") or "",
            "gender": getattr(u, "gender", "") or "",
            "birthday": fmt_dt_local(getattr(u, "birthday", None)).split(" ")[0] if getattr(u, "birthday", None) else "",
            "register_date": fmt_dt_local(getattr(u, "register_date", None)),
            "last_login": fmt_dt_local(getattr(u, "last_login", None)),
            "status": getattr(u, "status", "") or "",
            "avatar": build_avatar_url(getattr(u, "avatar", None)) if hasattr(u, "avatar") else None,
        })
    return jsonify({"ok": True, "data": data})

# ✅ 停用／啟用 API
@bp.post("/api/users/<int:user_id>/status", endpoint="api_user_status")
@admin_required_api
def api_user_status(user_id: int):
    u = User.query.get(user_id)
    if not u:
        return jsonify({"ok": False, "msg": "找不到使用者"}), 404

    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "").lower()
    if action not in ("disable", "enable"):
        return jsonify({"ok": False, "msg": "action 必須為 disable 或 enable"}), 400

    u.status = "disabled" if action == "disable" else "active"
    db.session.commit()
    return jsonify({"ok": True, "msg": "狀態已更新", "new_status": u.status})

# =========================
# ⭐⭐ API：刪除使用者（你的刪除鈕要的）
# =========================
@bp.post("/api/users/<int:user_id>/delete")
@admin_required_api
def api_user_delete(user_id):
   u = User.query.get(user_id)
   if not u:
       return jsonify({"ok": False, "msg": "找不到使用者"}), 404

   db.session.delete(u)
   db.session.commit()
   return jsonify({"ok": True, "msg": "使用者已刪除"})


@bp.get("/logout")
def logout():
    session.pop("admin", None)
    flash("你已登出管理員")
    return redirect(url_for("index"))

@bp.get("/seed", endpoint="seed_admin_route")
def seed_admin_route():
    username = "admin"
    plain = "Admin1234"
    if Admin.query.filter_by(username=username).first():
        return jsonify({"ok": True, "msg": f"已存在管理員 {username}，可直接登入。"})
    admin = Admin(username=username, password_hash=generate_password_hash(plain))
    db.session.add(admin)
    db.session.commit()
    return jsonify({"ok": True, "msg": f"已建立管理員 {username}，密碼：{plain}"}), 201

@bp.get("/_debug/templates")
def debug_templates():
    try:
        templates = sorted(current_app.jinja_env.list_templates())
    except Exception as e:
        return f"列模板發生錯誤：{e}", 500
    return "<pre>" + "\n".join(templates) + "</pre>"

@bp.get("/_debug/ping")
def debug_ping():
    return jsonify({
        "ok": True,
        "bp": "admin",
        "template_folder": current_app.template_folder,
        "static_folder": current_app.static_folder
    })
@bp.get("/answer-records")
@admin_required
def page_answer_records():
    """
    後台『答題紀錄』頁面
    對應模板：services/templates/ADMIN/answer-records.html
    """
    return render_template("ADMIN/answer-records.html")
@bp.get("/api/answer-records")
@admin_required_api
def api_answer_records():
    """
    回傳每位用戶在『系統題』的最近作答時間與回合總數（僅統計「有作答」的回合）
    - 最近作答時間：該用戶「有作答」的回合中的 MAX(ss.start_time)（不做時區轉換）
    - 回合總數：該用戶「有作答」的 SystemSession 筆數
      （有作答＝SystemRecord 內存在 user_answer 非空且不等於 '未作答'）
    """
    sql = text("""
        SELECT
            u.user_id,
            u.name,
            u.email,
            u.avatar AS avatar_raw,
            sa.last_start_time,
            COALESCE(sa.rounds, 0) AS rounds
        FROM `User` u
        LEFT JOIN (
            SELECT
                ss.user_id,
                MAX(ss.start_time) AS last_start_time,
                COUNT(*)          AS rounds
            FROM `SystemSession` ss
            WHERE EXISTS (
                SELECT 1
                FROM `SystemRecord` sr
                WHERE sr.ss_id = ss.ss_id
                  AND sr.user_answer IS NOT NULL
                  AND TRIM(sr.user_answer) <> ''
                  AND TRIM(sr.user_answer) <> '未作答'
            )
            GROUP BY ss.user_id
        ) AS sa
          ON sa.user_id = u.user_id
        ORDER BY (sa.last_start_time IS NULL), sa.last_start_time DESC
    """)

    rows = db.session.execute(sql).mappings().all()

    def _fmt_no_tz(dt):
        try:
            return dt.strftime("%Y/%m/%d %H:%M") if dt else ""
        except Exception:
            return str(dt) if dt else ""

    data = []
    for r in rows:
        data.append({
            "user_id": int(r["user_id"]),
            "name": r.get("name") or "",
            "email": r.get("email") or "",
            "avatar": build_avatar_url(r.get("avatar_raw")),
            "last_start_time": _fmt_no_tz(r.get("last_start_time")),
            "rounds": int(r.get("rounds") or 0),
        })

    return jsonify({"ok": True, "data": data})

@bp.get("/api/user/<int:user_id>/sessions")
@admin_required_api
def api_admin_user_sessions(user_id: int):
    """
    回傳某位使用者的『有作答』回合清單：
    回傳欄位：round_no(依時間遞增編號)、ss_id、start_time、end_time、answered_cnt、correct_cnt、accuracy_pct
    依 start_time DESC 排序（最新在上）。
    """
    q = text("""
        WITH per_session AS (
          SELECT
            ss.ss_id,
            ss.user_id,
            ss.start_time,
            ss.end_time,
            SUM(CASE
                  WHEN sr.user_answer IS NOT NULL
                   AND TRIM(sr.user_answer) <> ''
                   AND TRIM(sr.user_answer) <> '未作答'
                THEN 1 ELSE 0 END) AS answered_cnt,
            SUM(CASE
                  WHEN sr.user_answer IS NOT NULL
                   AND TRIM(sr.user_answer) <> ''
                   AND TRIM(sr.user_answer) <> '未作答'
                   AND sr.is_correct = 1
                THEN 1 ELSE 0 END) AS correct_cnt
          FROM SystemSession ss
          LEFT JOIN SystemRecord sr ON sr.ss_id = ss.ss_id
          WHERE ss.user_id = :uid
          GROUP BY ss.ss_id, ss.user_id, ss.start_time, ss.end_time
          HAVING SUM(CASE
                       WHEN sr.user_answer IS NOT NULL
                        AND TRIM(sr.user_answer) <> ''
                        AND TRIM(sr.user_answer) <> '未作答'
                     THEN 1 ELSE 0 END) > 0
        ),
        numbered AS (
          SELECT
            ps.*,
            ROW_NUMBER() OVER (
              PARTITION BY ps.user_id
              ORDER BY ps.start_time ASC
            ) AS round_no
          FROM per_session ps
        )
        SELECT
          ss_id,
          start_time,
          end_time,
          round_no,
          answered_cnt,
          correct_cnt,
          100 * correct_cnt / NULLIF(answered_cnt, 0) AS accuracy_pct
        FROM numbered
        ORDER BY start_time DESC
    """)
    rows = db.session.execute(q, {"uid": user_id}).fetchall()

    def _fmt(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None
        except Exception:
            return str(dt) if dt else None

    data = []
    for ss_id, start_t, end_t, round_no, answered, correct, acc in rows:
        answered = int(answered or 0)
        correct  = int(correct  or 0)
        data.append({
            "round_no": int(round_no),
            "ss_id": int(ss_id),
            "start_time": _fmt(start_t),   # 不轉時區
            "end_time": _fmt(end_t),
            "answered_cnt": answered,
            "correct_cnt": correct,
            "accuracy_pct": round(float(acc or 0.0), 1)
        })
    return jsonify({"rows": data})

@bp.get("/api/session/<int:ss_id>/items")
@admin_required_api
def api_admin_session_items(ss_id: int):
    """
    回傳單一回合的題目明細＋摘要（本回合題數、答對數、答對率）
    """
    q = text("""
       SELECT
         sr.sr_id,
         sr.s_id,
         sr.is_correct,
         sr.user_answer,
         s.s_text,
         s.s_answer AS correct_answer,
         s.s_media_url,
         s.s_category,
         COALESCE(c.c_name, '') AS category_name
       FROM SystemRecord sr
       JOIN System s   ON s.s_id = sr.s_id
       LEFT JOIN Category c ON c.c_id = s.s_category
       WHERE sr.ss_id = :sid
       ORDER BY sr.sr_id ASC
    """)
    rows = db.session.execute(q, {"sid": ss_id}).fetchall()

    items, total, correct = [], 0, 0
    for r in rows:
        (sr_id, s_id, is_correct, user_answer, s_text,
         correct_answer, media_url, s_category, cat_name) = r
        answered = (user_answer is not None and user_answer.strip() and user_answer.strip() != "未作答")
        if answered:
            total += 1
            if is_correct:
                correct += 1
        items.append({
            "sr_id": int(sr_id),
            "s_id": int(s_id),
            "is_correct": bool(is_correct),
            "user_answer": user_answer,
            "s_text": s_text,
            "correct_answer": correct_answer,
            "s_media_url": media_url,
            "s_category": s_category,
            "category_name": cat_name
        })

    acc_pct = round(100.0 * correct / total, 1) if total > 0 else 0.0
    return jsonify({"items": items, "summary": {"total": total, "correct": correct, "acc_pct": acc_pct}})

# （預留）詳細情形頁面骨架：之後再補內容
@bp.get("/answer-records/<int:user_id>")
@admin_required
def page_answer_record_detail(user_id: int):
    """
    預留的『詳細情形』頁面；先放簡單占位，之後你要的內容再補
    """
    return render_template("ADMIN/answer-record-detail.html", user_id=user_id)