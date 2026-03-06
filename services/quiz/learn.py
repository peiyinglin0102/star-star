# -*- coding: utf-8 -*-
"""
學習歷程頁面 Blueprint
負責顯示使用者的學習紀錄、進度等資料
"""
from flask import Blueprint, render_template, session, jsonify, abort, request
from sqlalchemy import text
from datetime import datetime, timedelta, timezone

learn_bp = Blueprint("learn", __name__, url_prefix="/learn")

# 取得 db（依你的專案實際路徑調整）
try:
    from auth import db
except Exception:
    from services.auth import db  # 備援

def _require_user_id():
    """從 session 取 user_id；若只有 email，就反查 user_id；否則 401。"""
    uid = session.get("user_id")
    if uid:
        return int(uid)
    email = session.get("user")
    if email:
        row = db.session.execute(
            text("SELECT user_id FROM User WHERE email = :email"),
            {"email": email}
        ).first()
        if row:
            return int(row[0])
    abort(401, description="未登入或找不到使用者 ID")
def _today_from_db_tpe():
    """
    回傳資料庫眼中的「台北今天」日期（date 物件）。
    用 UTC_TIMESTAMP() 統一來源，再轉 +08:00，避免連線時區影響。
    """
    row = db.session.execute(text("""
        SELECT DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00'))
    """)).first()
    return row[0]  # datetime.date

@learn_bp.route("/")
def learning_process():
    user = session.get("user_name", "訪客")
    return render_template("USER/Learningprocess.html", user=user)
@learn_bp.get("/api/radar/emotion")
def api_radar_emotion():
    uid = _require_user_id()

    # 固定順序
    EMO6 = ["開心", "難過", "生氣", "害怕", "驚訝", "厭惡"]

    q = text("""
        SELECT
          c.c_name,
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
              THEN 1 ELSE 0 END) AS total_cnt,
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
                 AND sr.is_correct = 1
              THEN 1 ELSE 0 END) AS correct_cnt
        FROM SystemRecord sr
        JOIN SystemSession ss ON ss.ss_id = sr.ss_id AND ss.user_id = :uid
        JOIN System s        ON s.s_id  = sr.s_id
        JOIN Category c      ON c.c_id  = s.s_category
        WHERE c.category_group = 'emotion'
          AND (s.s_type IS NULL OR s.s_type <> '情境')   -- 圖片/動圖等情緒題
        GROUP BY c.c_name
    """)

    rows = db.session.execute(q, {"uid": uid}).fetchall()
    # 做成 {類別名: (total, correct)}
    stat = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}

    labels, values = [], []
    for name in EMO6:
        total, correct = stat.get(name, (0, 0))
        acc = round(100.0 * correct / total, 1) if total > 0 else 0.0
        labels.append(name); values.append(acc)

    return jsonify({"labels": labels, "values": values})


@learn_bp.get("/api/radar/situation")
def api_radar_situation():
    uid = _require_user_id()

    # 固定順序
    SIT6 = ["社交規範", "安全意識", "生活習慣", "身體覺察", "情緒管理", "禮貌表達"]

    q = text("""
        SELECT
          c.c_name,
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
              THEN 1 ELSE 0 END) AS total_cnt,
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
                 AND sr.is_correct = 1
              THEN 1 ELSE 0 END) AS correct_cnt
        FROM SystemRecord sr
        JOIN SystemSession ss ON ss.ss_id = sr.ss_id AND ss.user_id = :uid
        JOIN System s        ON s.s_id  = sr.s_id
        JOIN Category c      ON c.c_id  = s.s_category
        WHERE c.category_group = 'situation'
          AND s.s_type = '情境'                           -- 只算情境題
        GROUP BY c.c_name
    """)

    rows = db.session.execute(q, {"uid": uid}).fetchall()
    stat = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}

    labels, values = [], []
    for name in SIT6:
        total, correct = stat.get(name, (0, 0))
        acc = round(100.0 * correct / total, 1) if total > 0 else 0.0
        labels.append(name); values.append(acc)

    return jsonify({"labels": labels, "values": values})


# ====== KPI：總答題數 + 總答對率（％） ======
@learn_bp.get("/api/kpis")
def api_kpis():
    uid = _require_user_id()

    q_kpi = text("""
        SELECT
          -- 作答過的題目數（未作答不算）
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
                THEN 1 ELSE 0
              END) AS answered_cnt,
          -- 答對題數（只計入有作答且答對）
          SUM(CASE
                WHEN sr.user_answer IS NOT NULL
                 AND TRIM(sr.user_answer) <> ''
                 AND TRIM(sr.user_answer) <> '未作答'
                 AND sr.is_correct = 1
                THEN 1 ELSE 0
              END) AS correct_cnt
        FROM SystemRecord sr
        JOIN SystemSession ss ON ss.ss_id = sr.ss_id
        WHERE ss.user_id = :uid
    """)

    row = db.session.execute(q_kpi, {"uid": uid}).first()
    answered = int((row.answered_cnt or 0))
    correct  = int((row.correct_cnt  or 0))
    acc_pct  = round(100.0 * correct / answered, 1) if answered > 0 else 0.0

    return jsonify({
        "total_answered": answered,      # ← 不含未作答
        "total_correct":  correct,
        "overall_accuracy_pct": acc_pct  # ← 以 answered 為分母
    })


@learn_bp.get("/api/session/<int:ss_id>/items")
def api_session_items(ss_id: int):
    uid = _require_user_id()

    # 授權：確認這個 SystemSession 屬於該使用者
    own = db.session.execute(
        text("SELECT 1 FROM SystemSession WHERE ss_id = :sid AND user_id = :uid"),
        {"sid": ss_id, "uid": uid}
    ).first()
    if not own:
        abort(403, description="無權限查看該回合")

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


# ---------- 趨勢（週：最近 7 天；年：最近 12 個月） ----------
@learn_bp.get("/api/trend/weekly")
def api_trend_weekly():
    """
    回傳「一週」的答對率趨勢。
    - 仍以 UTC 日期彙總（不做任何時區轉換）
    - 新增 ?offset= 整數參數（0=本週/最近一週，-1=上一週，-2=上上週...）
    - 不允許 offset > 0（超過現在的未來週），一律壓回 0
    """
    uid = _require_user_id()

    # 這位使用者最近一次作答的 UTC 日期；若沒有資料就用 UTC 今天
    last_date = db.session.execute(text("""
        SELECT COALESCE(
                 MAX(DATE(ss.start_time)),
                 UTC_DATE()
               ) AS last_d
        FROM SystemSession ss
        WHERE ss.user_id = :uid
          AND ss.start_time < UTC_TIMESTAMP() + INTERVAL 1 DAY
    """), {"uid": uid}).scalar()

    # 讀 offset（0=最近一週，-1=上一週...；任何 >0 都壓成 0，避免看未來）
    try:
        offset = int(request.args.get("offset", 0))
    except Exception:
        offset = 0
    if offset > 0:
        offset = 0

    # 以「最近作答日」為基準，偏移 offset 週
    anchor_last = last_date + timedelta(days=7 * offset)

    # 週區間（共 7 天）
    start_date = anchor_last - timedelta(days=6)
    end_date_exclusive = anchor_last + timedelta(days=1)  # [start, end)

    # 撈這 7 天資料（仍以 UTC 日界線）
    q = text("""
        SELECT
          DATE(ss.start_time) AS d_utc,
          100 * SUM(CASE
                      WHEN sr.user_answer IS NOT NULL
                       AND TRIM(sr.user_answer) <> ''
                       AND TRIM(sr.user_answer) <> '未作答'
                       AND sr.is_correct = 1
                    THEN 1 ELSE 0 END)
              / NULLIF(
                  SUM(CASE
                        WHEN sr.user_answer IS NOT NULL
                         AND TRIM(sr.user_answer) <> ''
                         AND TRIM(sr.user_answer) <> '未作答'
                      THEN 1 ELSE 0 END), 0
                ) AS accuracy_pct
        FROM SystemRecord sr
        JOIN SystemSession ss ON ss.ss_id = sr.ss_id
        WHERE ss.user_id = :uid
          AND ss.start_time >= :start_dt
          AND ss.start_time <  :end_dt
        GROUP BY DATE(ss.start_time)
        ORDER BY d_utc
    """)
    rows = db.session.execute(q, {
        "uid": uid,
        "start_dt": start_date,
        "end_dt": end_date_exclusive
    }).fetchall()

    # 補齊 7 天座標軸
    days = [start_date + timedelta(days=i) for i in range(7)]
    mp = {r[0]: float(r[1] or 0.0) for r in rows}

    labels = [f"{d.month:02d}/{d.day:02d}" for d in days]
    values = [round(mp.get(d, 0.0), 1) for d in days]
    range_label = f"{labels[0]}–{labels[-1]}"

    # 是否已經在最新一週（offset==0 時）
    is_latest = (offset == 0)

    return jsonify({
        "labels": labels,
        "values": values,
        "range": range_label,
        "offset": offset,
        "is_latest": is_latest
    })


@learn_bp.get("/api/trend/yearly")
def api_trend_yearly():
    uid = _require_user_id()

    # 依 UTC 月界線彙總最近 12 個月（含本月）
    q = text("""
        SELECT
          DATE_FORMAT(ss.start_time, '%Y-%m') AS ym_utc,  -- 以 UTC 月界線
          100 * SUM(CASE
                      WHEN sr.user_answer IS NOT NULL
                       AND TRIM(sr.user_answer) <> ''
                       AND TRIM(sr.user_answer) <> '未作答'
                       AND sr.is_correct = 1
                    THEN 1 ELSE 0 END)
              / NULLIF(
                  SUM(CASE
                        WHEN sr.user_answer IS NOT NULL
                         AND TRIM(sr.user_answer) <> ''
                         AND TRIM(sr.user_answer) <> '未作答'
                      THEN 1 ELSE 0 END), 0
                ) AS accuracy_pct
        FROM SystemRecord sr
        JOIN SystemSession ss ON ss.ss_id = sr.ss_id
        WHERE ss.user_id = :uid
          AND ss.start_time >= DATE_FORMAT(DATE_SUB(UTC_TIMESTAMP(), INTERVAL 11 MONTH), '%Y-%m-01')
        GROUP BY DATE_FORMAT(ss.start_time, '%Y-%m')
        ORDER BY ym_utc
    """)
    rows = db.session.execute(q, {"uid": uid}).fetchall()

    # 用資料庫的 UTC 現在時間組出 12 個連續月份（UTC）
    now_utc = db.session.execute(text("SELECT UTC_TIMESTAMP()")).scalar()
    seq = []
    y, m = now_utc.year, now_utc.month
    for i in range(11, -1, -1):
        yy = y if m - i > 0 else y - 1
        mm = ((m - i - 1) % 12) + 1
        seq.append(f"{yy}-{mm:02d}")

    mp = {r[0]: float(r[1] or 0.0) for r in rows}
    labels = [f"{int(s[5:7])}月" for s in seq]
    values = [round(mp.get(s, 0.0), 1) for s in seq]
    return jsonify({"labels": labels, "values": values})


# ---------- 回合摘要（分頁） ----------
@learn_bp.get("/api/sessions")
def api_sessions():
    uid = _require_user_id()
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    offset = max(0, int(request.args.get("offset", 0)))

    # 先把每個回合彙總（只算已作答；未作答不納入分母）
    # 再用 ROW_NUMBER() 依「該使用者」start_time 由舊到新編 round_no
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
          JOIN SystemRecord sr ON sr.ss_id = ss.ss_id
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
          correct_cnt,
          100 * correct_cnt / NULLIF(answered_cnt, 0) AS accuracy_pct
        FROM numbered
        ORDER BY start_time DESC
        LIMIT :limit OFFSET :offset
    """)
    rows = db.session.execute(q, {"uid": uid, "limit": limit, "offset": offset}).fetchall()

    # 總筆數（這位使用者的有效回合數）
    q_cnt = text("""
        WITH per_session AS (
          SELECT ss.ss_id
          FROM SystemSession ss
          JOIN SystemRecord sr ON sr.ss_id = ss.ss_id
          WHERE ss.user_id = :uid
          GROUP BY ss.ss_id
          HAVING SUM(CASE
                       WHEN sr.user_answer IS NOT NULL
                        AND TRIM(sr.user_answer) <> ''
                        AND TRIM(sr.user_answer) <> '未作答'
                     THEN 1 ELSE 0 END) > 0
        )
        SELECT COUNT(*) FROM per_session
    """)
    total_rows = int(db.session.execute(q_cnt, {"uid": uid}).scalar() or 0)

    data = []
    for r in rows:
        ss_id, start_t, end_t, round_no, correct_cnt, acc = r
        data.append({
            "round_no": int(round_no),                 # ← 使用者專屬第 N 回
            "ss_id": int(ss_id),                       # 仍保留給 modal 明細使用
            "start_time": start_t.strftime("%Y-%m-%d %H:%M:%S") if start_t else None,  # UTC 字串
            "end_time":   end_t.strftime("%Y-%m-%d %H:%M:%S") if end_t else None,      # UTC 字串
            "correct_cnt": int(correct_cnt or 0),
            "accuracy_pct": round(float(acc or 0.0), 1)
        })

    return jsonify({"rows": data, "total_rows": total_rows})

@learn_bp.get("/api/custom/sets")
def api_custom_sets():
    uid = _require_user_id()
    limit  = max(1, min(int(request.args.get("limit", 10)), 100))
    offset = max(0, int(request.args.get("offset", 0)))

    q = text("""
    WITH my_sets AS (
    SELECT set_id, set_title
    FROM CustomSet
    WHERE user_id = :uid
    ),
    agg AS (
    SELECT
        cs.set_id,
        MAX(cs.cs_start_time) AS last_answered_at,
        SUM(CASE
            WHEN cr.cr_user_answer IS NOT NULL
            AND TRIM(cr.cr_user_answer) <> ''
            AND TRIM(cr.cr_user_answer) <> '未作答'
            THEN 1 ELSE 0 END) AS answered_cnt,
        SUM(CASE
            WHEN cr.cr_user_answer IS NOT NULL
            AND TRIM(cr.cr_user_answer) <> ''
            AND TRIM(cr.cr_user_answer) <> '未作答'
            AND cr.cr_is_correct = 1
            THEN 1 ELSE 0 END) AS correct_cnt
    FROM CustomSession cs
    JOIN CustomRecord  cr ON cr.cs_id = cs.cs_id   -- ← 用 JOIN，確保是有紀錄的回合
    GROUP BY cs.set_id
    )
    SELECT
    ms.set_id,
    COALESCE(ms.set_title, CONCAT('題組#', ms.set_id)) AS set_name,
    100.0 * COALESCE(a.correct_cnt,0) / NULLIF(COALESCE(a.answered_cnt,0), 0) AS accuracy_pct,
    a.last_answered_at
    FROM my_sets ms
    LEFT JOIN agg a ON a.set_id = ms.set_id
    ORDER BY ms.set_title IS NULL, ms.set_title, ms.set_id
    LIMIT :limit OFFSET :offset;
    """)

    rows = db.session.execute(q, {"uid": uid, "limit": limit, "offset": offset}).fetchall()

    total_rows = int(db.session.execute(
        text("SELECT COUNT(*) FROM CustomSet WHERE user_id = :uid"),
        {"uid": uid}
    ).scalar() or 0)

    data = []
    for set_id, set_name, acc, last_at in rows:
        data.append({
            "set_id": int(set_id),
            "set_name": set_name,
            "accuracy_pct": round(float(acc or 0.0), 1),
            "last_answered_at": last_at.strftime("%Y-%m-%d %H:%M:%S") if last_at else None
        })
    return jsonify({"rows": data, "total_rows": total_rows})


@learn_bp.get("/api/custom/set/<int:set_id>/summary")
def api_custom_set_summary(set_id: int):
    uid = _require_user_id()

    # 授權：題組必須屬於該使用者
    own = db.session.execute(
        text("SELECT 1 FROM CustomSet WHERE set_id = :sid AND user_id = :uid"),
        {"sid": set_id, "uid": uid}
    ).first()
    if not own:
        abort(403, description="無權限查看該題組")

    # 題組題數
    question_cnt = int(db.session.execute(
        text("SELECT COUNT(*) FROM Custom WHERE set_id = :sid"),
        {"sid": set_id}
    ).scalar() or 0)

    # 練習次數（不靠 user_id）
    practice_cnt = int(db.session.execute(
        text("SELECT COUNT(*) FROM CustomSession WHERE set_id = :sid"),
        {"sid": set_id}
    ).scalar() or 0)

    # 最近一次回合 → 本回合題數
    latest_cs_id = db.session.execute(text("""
        SELECT cs.cs_id
        FROM CustomSession cs
        JOIN CustomRecord  cr ON cr.cs_id = cs.cs_id
        WHERE cs.set_id = :sid
        GROUP BY cs.cs_id
        ORDER BY MAX(cs.cs_start_time) DESC
        LIMIT 1
    """), {"sid": set_id}).scalar()

    latest_question_cnt = 0
    if latest_cs_id:
        latest_question_cnt = int(db.session.execute(text("""
            SELECT COUNT(*) FROM CustomRecord WHERE cs_id = :csid
        """), {"csid": latest_cs_id}).scalar() or 0)

    # 整體答對率（跨所有回合；不靠 user_id）
    row = db.session.execute(text("""
        SELECT
          SUM(CASE
                WHEN cr.cr_user_answer IS NOT NULL
                 AND TRIM(cr.cr_user_answer) <> ''
                 AND TRIM(cr.cr_user_answer) <> '未作答'
              THEN 1 ELSE 0 END) AS answered_cnt,
          SUM(CASE
                WHEN cr.cr_user_answer IS NOT NULL
                 AND TRIM(cr.cr_user_answer) <> ''
                 AND TRIM(cr.cr_user_answer) <> '未作答'
                 AND cr.cr_is_correct = 1
              THEN 1 ELSE 0 END) AS correct_cnt
        FROM CustomSession cs
        LEFT JOIN CustomRecord cr ON cr.cs_id = cs.cs_id
        WHERE cs.set_id = :sid
    """), {"sid": set_id}).first()

    answered_cnt = int((row.answered_cnt or 0))
    correct_cnt  = int((row.correct_cnt  or 0))
    accuracy_pct = round(100.0 * correct_cnt / answered_cnt, 1) if answered_cnt > 0 else 0.0

    return jsonify({
        "set_id": set_id,
        "summary": {
            "question_cnt":        question_cnt,
            "practice_cnt":        practice_cnt,
            "latest_question_cnt": latest_question_cnt,
            "accuracy_pct":        accuracy_pct
        }
    })

@learn_bp.get("/api/custom/set/<int:set_id>/longterm")
def api_custom_set_longterm(set_id: int):
    uid = _require_user_id()

    # 授權
    own = db.session.execute(
        text("SELECT 1 FROM CustomSet WHERE set_id = :sid AND user_id = :uid"),
        {"sid": set_id, "uid": uid}
    ).first()
    if not own:
        abort(403, description="無權限查看該題組")

    q = text("""
      SELECT
        c.c_id,
        c.c_text,
        c.c_answer AS correct_answer,
        c.c_media_url,
        SUM(CASE
              WHEN cr.cr_user_answer IS NOT NULL
               AND TRIM(cr.cr_user_answer) <> ''
               AND TRIM(cr.cr_user_answer) <> '未作答'
            THEN 1 ELSE 0 END) AS answered_cnt,
        SUM(CASE
              WHEN cr.cr_user_answer IS NOT NULL
               AND TRIM(cr.cr_user_answer) <> ''
               AND TRIM(cr.cr_user_answer) <> '未作答'
               AND cr.cr_is_correct = 1
            THEN 1 ELSE 0 END) AS correct_cnt
      FROM Custom c
      LEFT JOIN CustomRecord  cr ON cr.c_id  = c.c_id
      LEFT JOIN CustomSession cs ON cs.cs_id = cr.cs_id
      WHERE c.set_id = :sid
        AND (cs.set_id IS NULL OR cs.set_id = :sid)  -- 安全保險：限制在同一題組
      GROUP BY c.c_id, c.c_text, c.c_answer, c.c_media_url
      ORDER BY c.c_id
    """)
    rows = db.session.execute(q, {"sid": set_id}).fetchall()

    items = []
    for c_id, c_text, c_answer, media, answered_cnt, correct_cnt in rows:
        answered_cnt = int(answered_cnt or 0)
        correct_cnt  = int(correct_cnt  or 0)
        acc_pct = round(100.0 * correct_cnt / answered_cnt, 1) if answered_cnt > 0 else 0.0
        items.append({
            "c_id": int(c_id),
            "c_text": c_text,
            "correct_answer": c_answer,
            "c_media_url": media,
            "answered_cnt": answered_cnt,
            "correct_cnt": correct_cnt,
            "acc_pct": acc_pct
        })
    return jsonify({"items": items})
