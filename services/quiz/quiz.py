# -*- coding: utf-8 -*-
"""
星星關卡（DB 版）
把原本的獨立 Flask app 改成 Blueprint，讓主聚合 app 註冊使用。
路由前綴由主程式決定（/quiz）。
"""
from __future__ import annotations
from flask import Blueprint, render_template, request, jsonify, session, url_for
import os, sys, random, threading, datetime

# === 導入 DB 連線 ===
sys.path.append(os.path.dirname(__file__))
from .db_config import get_conn

# === 路徑設定（沿用原本的 templates/USER 與 static）===
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "templates", "USER"))
STATIC_DIR   = os.path.abspath(os.path.join(BASE_DIR, "..", "static"))
RC_FILE      = os.path.join(BASE_DIR, ".round_counter")

bp = Blueprint(
  "quiz", __name__,
  template_folder=TEMPLATE_DIR,
  static_folder=STATIC_DIR
  # 不要設 static_url_path，避免與主程式 /static 衝突
)


# ===== 參數（保留你原本邏輯需要） =====
TOTAL_QUESTIONS = 10
PRIORITY_MIN, PRIORITY_MAX = 2, 3
COOLDOWN_ROUNDS = 2
SMOOTH_NEW_RATE = 0.5
ALPHA, BETA, GAMMA = 0.7, 0.2, 0.1

# 題庫的情緒字用這組；含常見別名對應
CATEGORIES = ["開心", "難過", "生氣", "驚訝", "害怕", "厭惡"]
EMO_ALIASES = {
    "快樂": "開心", "高興": "開心",
    "悲傷": "難過", "傷心": "難過",
    "憤怒": "生氣",
    "恐懼": "害怕",

    

    # 其他相同字面就不轉
}

ROUND_COUNTER_LOCK = threading.Lock()
_last_used_round = {}

# ===== 檔案初始化 =====
def _ensure_files():
    if not os.path.exists(RC_FILE):
        with open(RC_FILE, "w", encoding="utf-8") as f:
            f.write("0")

def increment_round_counter():
    with ROUND_COUNTER_LOCK:
        if not os.path.exists(RC_FILE):
            with open(RC_FILE, "w", encoding="utf-8") as f:
                f.write("0")
        with open(RC_FILE, "r+", encoding="utf-8") as f:
            v = int((f.read().strip() or "0")) + 1
            f.seek(0); f.truncate(); f.write(str(v))
            return v
# === AI Feedback (OpenAI) ===
# 優先使用 OpenAI 產生一句簡短、溫暖、適合孩童的回饋；失敗則回退預設話術
try:
    # openai>=1.0.0 SDK
    from openai import OpenAI  # type: ignore
    _oa_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    _oa_client = None
# === 保留你原本的預設話術（我只把文字拉到這裡集中管理） ===
_DEFAULT_OK = "你好棒！這題你答對了，是開心的表情沒錯！"
_DEFAULT_NG = "這個表情好像不是這樣喔，再想一想看看~"

def generate_feedback(is_correct: bool, category_hint: str | None = None) -> str:
    """
    【備援用】產不出 AI 回饋時，使用預設鼓勵語。
    """
    # 原本寫死的內容（保留）
    # default_ok = "你好棒！這題你答對了，是開心的表情沒錯！"
    # default_ng = "這個表情好像不是這樣喔，再想一想看看~"
    return _DEFAULT_OK if is_correct else _DEFAULT_NG
# === 新增：優先使用 OpenAI 產生單句鼓勵語 ===


# === 判斷是否為「情境 / 社交」題的小工具 ===
def is_situation_question_from_row(row: dict) -> bool:
   """
   綜合 s_type / answer / category_group 判斷是不是「情境題」。
   適用於 fake_questions.json 中的單題資料。
   """
   s_type = (row.get("s_type") or "").strip().lower()
   ans    = (row.get("answer") or "").strip()
   cg     = (row.get("category_group") or "").strip().lower()


   # 1) 題型名稱顯示是情境
   if "情境" in s_type:
       return True
   if s_type in ("scene", "situation", "social"):
       return True


   # 2) 類別群組標成情境（若 JSON 有提供）
   if cg == "situation":
       return True


   # 3) 答案是 True/False 題
   if ans in ("正確", "錯誤"):
       return True


   return False




# === 優先使用 OpenAI 產生單句鼓勵語（整合提示詞規則） ===
def generate_feedback_ai(
   is_correct: bool,
   *,
   s_type: str | None = None,
   question_text: str | None = None,
   correct_answer: str | None = None,
   selected_answer: str | None = None,
   category_hint: str | None = None,   # 可放 emotion / situation
   model: str | None = None,
   timeout_seconds: int = 6,
) -> str | None:
   """
   生成「一句話」的回饋（繁體中文、適合孩童、8~25 字）：
   - 正確：誇讚
   - 錯誤：
       - 情緒題：提示表情特徵
       - 情境題：提示正確行為


   任一失敗回傳 None（讓上層回退預設話術）。
   """
   if _oa_client is None:
       return None


   mdl = model or os.getenv("OPENAI_FEEDBACK_MODEL") or "gpt-4o-mini"


   # === 整理題目型態，判斷是否為情境題 ===
   raw_type = (s_type or "").strip().lower()
   raw_ans  = (correct_answer or "").strip()
   cg       = (category_hint or "").strip().lower()

   # ⭐ 情境題設定（以答案是否是 正確/錯誤 為主）
   is_situation = False
   if raw_ans in ("正確", "錯誤"):
       is_situation = True
   elif "情境" in raw_type:
       is_situation = True
   elif cg == "situation":
       is_situation = True

   # ===== 產生規則 =====
   if is_correct:
       mode_rules = (
           "你是一位親切的小老師。孩子答對了題目：\n"
           "1) 用溫暖、肯定、自然的語氣稱讚。\n"
           "2) 不重複題目內容。\n"
           "3) 字數 8~20 個中文字，標點只能用句號或逗號，不可有 emoji。\n"
       )
   else:
       if is_situation:
           mode_rules = (
               "你是一位溫柔的小老師。孩子剛剛答錯了一題社交情境題：\n"
               "1) 要直接給出正確行為提示。\n"
               "2) 用具體簡單的方式指引他下一步應該怎麼做。\n"
               "3) 例如：「要等綠燈才能過馬路喔。」\n"
               "4) 句子 8~25 字，不可有 emoji。\n"
           )
       else:
           mode_rules = (
               "你是一位溫柔的小老師。孩子剛剛答錯了一題情緒辨識題：\n"
               "1) 給出該情緒的外觀線索（例如嘴角、眼睛）。\n"
               "2) 引導孩子觀察此特徵。\n"
               "3) 句子 8~25 字，不可有 emoji。\n"
           )

   sys_msg = (
       "你是一位溫柔且鼓勵孩童的老師，使用繁體中文回覆。\n"
       "受眾年齡約 3–7 歲，請用簡單、自然、具體的語氣。\n"
       + mode_rules +
       "通用規則：只輸出一句話，不要加入多段或清單。\n"
   )

   user_msg_text = (
       f"題型：{'情境' if is_situation else '情緒'}\n"
       f"答題結果：{'答對' if is_correct else '答錯'}\n"
       f"正確答案：{raw_ans}\n"
       f"孩子的作答：{selected_answer}\n"
       f"題幹摘要：{question_text}\n"
       "請根據規則生成一段適合幼兒的回饋語。"
   )

   try:
       client = _oa_client.with_options(timeout=timeout_seconds)
       resp = client.chat.completions.create(
           model=mdl,
           temperature=0.6,
           max_tokens=60,
           messages=[
               {"role": "system", "content": sys_msg},
               {"role": "user", "content": user_msg_text},
           ],
       )
       text = (resp.choices[0].message.content or "").strip()
       if not text:
           return None
       if "。" in text:
           text = text.split("。", 1)[0] + "。"
       if len(text) < 6 or len(text) > 56:
           return None
       return text

   except Exception as e:
       print("⚠️ OpenAI 回饋失敗：", e)
       return None


# ===== 小工具 =====
def normalize_emotion(ans: str) -> str:
    a = (ans or "").strip()
    return EMO_ALIASES.get(a, a)

def normalize_tf(v: str) -> str:
    v = (v or "").strip()
    if v in ("正確", "對", "是", "True", "true", "1"):  return "正確"
    if v in ("錯誤", "錯", "否", "False", "false", "0"): return "錯誤"
    return v

def detect_media_type(url: str) -> str:
    """依副檔名猜 media 類型：image / video"""
    u = (url or "").lower()
    if u.endswith((".mp4", ".webm", ".mov", ".m4v")):
        return "video"
    # GIF 靜態也能用 <img>；如需 video-like 播放可自行判斷
    return "image"

# ===== DB 抓題（改為從 View 出題；無資料時備援抓 System） =====
def _rows_from_view(user_id: int, s_type: str | None = None):
    """
    從 v_questions_excluding_last2 取該使用者可出題清單，並連個人/全域統計。
    """
    base_sql = """
        SELECT
            v.s_id                          AS id,
            v.s_type                        AS s_type,
            v.s_text                        AS text,
            v.s_answer                      AS answer,
            v.s_media_url                   AS img,
            COALESCE(s.s_count, 0)          AS s_count_g,
            COALESCE(s.s_correct_count, 0)  AS s_correct_count_g,
            COALESCE(u.attempt_u, 0)        AS attempt_u,
            COALESCE(u.correct_u, 0)        AS correct_u,
            COALESCE(l.last_round_u, 0)     AS last_round_u_user,
            COALESCE(s.last_used_round, 0)  AS last_round_g
        FROM v_questions_excluding_last2 AS v
        JOIN `System` AS s
          ON s.s_id = v.s_id
        LEFT JOIN (
            SELECT sr.s_id, COUNT(*) AS attempt_u, SUM(sr.is_correct) AS correct_u
            FROM SystemRecord sr JOIN SystemSession ss ON ss.ss_id = sr.ss_id
            WHERE ss.user_id = %s
            GROUP BY sr.s_id
        ) AS u ON u.s_id = v.s_id
        LEFT JOIN (
            SELECT sr.s_id, MAX(ss.ss_id) AS last_round_u
            FROM SystemRecord sr JOIN SystemSession ss ON ss.ss_id = sr.ss_id
            WHERE ss.user_id = %s
            GROUP BY sr.s_id
        ) AS l ON l.s_id = v.s_id
        WHERE v.user_id = %s
        AND s.is_active = 1
    """
    args = [user_id, user_id, user_id]
    if s_type:
        base_sql += " AND v.s_type = %s"
        args.append(s_type)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(base_sql, args)
            rows = cur.fetchall() or []
    for r in rows:
        r["answer"] = (r.get("answer") or "").strip()
    return rows

def _rows_from_system_all(s_type: str | None = None, user_id: int | None = None):
    """
    備援：若 view 查不到（例如新用戶無回合），回退抓整個 System，仍補齊 user/global 統計。
    """
    sql = """
        SELECT
            s.s_id                          AS id,
            s.s_type                        AS s_type,
            s.s_text                        AS text,
            s.s_answer                      AS answer,
            s.s_media_url                   AS img,
            COALESCE(s.s_count, 0)          AS s_count_g,
            COALESCE(s.s_correct_count, 0)  AS s_correct_count_g,
            COALESCE(u.attempt_u, 0)        AS attempt_u,
            COALESCE(u.correct_u, 0)        AS correct_u,
            COALESCE(l.last_round_u, 0)     AS last_round_u_user,
            COALESCE(s.last_used_round, 0)  AS last_round_g
        FROM `System` AS s
        LEFT JOIN (
            SELECT sr.s_id, COUNT(*) AS attempt_u, SUM(sr.is_correct) AS correct_u
            FROM SystemRecord sr JOIN SystemSession ss ON ss.ss_id = sr.ss_id
            WHERE ss.user_id = %s
            GROUP BY sr.s_id
        ) AS u ON u.s_id = s.s_id
        LEFT JOIN (
            SELECT sr.s_id, MAX(ss.ss_id) AS last_round_u
            FROM SystemRecord sr JOIN SystemSession ss ON ss.ss_id = sr.ss_id
            WHERE ss.user_id = %s
            GROUP BY sr.s_id
        ) AS l ON l.s_id = s.s_id
         WHERE s.is_active = 1
    """
    args = [user_id or 0, user_id or 0]
    if s_type:
        sql += " AND s.s_type = %s"
        args.append(s_type)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() or []
    for r in rows:
        r["answer"] = (r.get("answer") or "").strip()
    return rows

def load_questions_from_db(user_id: int | None = None, s_type: str | None = None):
    uid = user_id or 0
    rows = _rows_from_view(uid, s_type)
    if not rows:
        rows = _rows_from_system_all(s_type, uid)
    return rows

# ===== 正確率（個人優先、全域平滑） =====
def calculate_rate(q):
    att_u = int(q.get("attempt_u", 0) or 0)
    cor_u = int(q.get("correct_u", 0) or 0)
    att_g = int(q.get("s_count_g", 0) or 0)
    cor_g = int(q.get("s_correct_count_g", 0) or 0)

    rate_user = (cor_u / att_u) if att_u > 0 else None
    rate_global = (cor_g / att_g) if att_g > 0 else 0.5

    if rate_user is None:
        return rate_global
    if att_u < 5:
        lam = att_u / 5.0
        return lam * rate_user + (1 - lam) * rate_global
    return rate_user

# ===== 權重（個人冷卻 + 錯誤率優先） =====
def compute_weight(q, current_round):
    rate = calculate_rate(q)
    wrong_rate = 1 - rate
    attempts = int(q.get("attempt_u", 0) or 0)
    last_used = int(q.get("last_round_u_user", 0) or -999)
    diff = current_round - last_used
    recency_penalty = 0.0 if diff <= COOLDOWN_ROUNDS else 1.0
    return max(ALPHA * wrong_rate + BETA * (1 / (1 + attempts)) + GAMMA * recency_penalty, 0.001)

def weighted_sample_no_replace(items, weights, k):
    items, w = list(items), list(weights)
    selected = []
    for _ in range(min(k, len(items))):
        total = sum(w)
        if total <= 0:
            break
        r = random.random() * total
        cum = 0
        for i, wi in enumerate(w):
            cum += wi
            if r <= cum:
                selected.append(items.pop(i))
                w.pop(i)
                break
    return selected

def guess_category(q):
    ans = normalize_emotion(q.get("answer"))
    return ans if ans in CATEGORIES else None

# ======== 路由 ========
@bp.get("/")
def play_page():
    s_type = request.args.get("s_type", "圖片")
    return render_template("Quiz.html", s_type=s_type, home_url=url_for("auth.home"))

# --- A) start_session ---
@bp.post("/start_session")
def start_session():
    for k in ("quiz_user_id", "quiz_start_time", "answers", "current_questions"):
        session.pop(k, None)

    uid = session.get("user_id") or session.get("auth_user_id") or session.get("uid")
    try:
        uid = int(uid) if uid is not None else None
    except Exception:
        uid = None
    if uid is not None:
        session["quiz_user_id"] = uid

    session["quiz_start_time"] = datetime.datetime.now().isoformat()
    session["answers"] = []
    session["current_questions"] = []
    session.modified = True
    return jsonify({"status": "started", "user_id": uid})

@bp.get("/play")
def play():
    s_type = request.args.get("s_type", "圖片")
    return render_template("Quiz.html", s_type=s_type, home_url=url_for("auth.home"))

@bp.get("/get_questions")
def get_questions():
    
    try:
        # 預設圖片題，避免混入沒有媒體的題型
        s_type = request.args.get("s_type", "圖片").strip()

        # 解析使用者 id 供個人化出題
        uid = (
            session.get("auth_user_id")
            or session.get("user_id")
            or session.get("uid")
            or session.get("quiz_user_id")
        )
        try:
            uid = int(uid) if uid is not None else 0
        except Exception:
            uid = 0

        questions = load_questions_from_db(user_id=uid, s_type=s_type or None)
        if not questions:
            return jsonify([])
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return jsonify({"error": "get_questions failed", "detail": str(e), "trace": tb}), 500

    current_round = increment_round_counter()
    # 1) 優先題
    k_priority = random.choice([PRIORITY_MIN, PRIORITY_MAX])
    wrong_sorted = sorted(questions, key=lambda x: 1 - calculate_rate(x), reverse=True)
    candidate_pool = wrong_sorted[: max(10, k_priority * 3)]
    candidate_weights = [compute_weight(q, current_round) for q in candidate_pool]
    priority_selected = weighted_sample_no_replace(candidate_pool, candidate_weights, k_priority)

    selected = priority_selected.copy()
    selected_ids = {q["id"] for q in selected}

    # 2) 類別輪替（僅情緒題才有類別）
    for cat in CATEGORIES:
        if len(selected) >= TOTAL_QUESTIONS: break
        cat_pool = [
            q for q in questions
            if q["id"] not in selected_ids
            and (q.get("s_type") != "情境")
            and guess_category(q) == cat
        ]
        if cat_pool:
            cw = [compute_weight(q, current_round) for q in cat_pool]
            pick = weighted_sample_no_replace(cat_pool, cw, 1)
            if pick:
                selected.append(pick[0]); selected_ids.add(pick[0]["id"])

    # 3) 補滿
    remain = TOTAL_QUESTIONS - len(selected)
    if remain > 0:
        pool = [q for q in questions if q["id"] not in selected_ids]
        if pool:
            pw = [compute_weight(q, current_round) for q in pool]
            more = weighted_sample_no_replace(pool, pw, remain)
            selected.extend(more)

    # 更新記憶體快取
    for q in selected:
        _last_used_round[q["id"]] = current_round

    # 4) 組輸出（情境題 => 正確/錯誤；情緒題 => 六大情緒干擾）
    out = []
    TF_PAIR = ("正確", "錯誤")

    for q in selected:
        s_type_q = (q.get("s_type") or "").strip()
        correct = normalize_tf(q.get("answer") or "")
        # 新版列名是 img；舊資料可能在 media_url → 兩者相容
        media_url = q.get("img") or q.get("media_url") or ""
        media_type = detect_media_type(media_url)

        if s_type_q == "情境" or correct in TF_PAIR:
            # 情境題：只出 True/False
            correct_norm = "正確" if correct == "正確" else "錯誤"
            other = "錯誤" if correct_norm == "正確" else "正確"
            opts = [correct_norm, other]
        else:
            # 情緒題：用六大情緒做干擾
            correct_emo = normalize_emotion(correct)
            distractors = [c for c in CATEGORIES if c != correct_emo] or CATEGORIES[:]
            distractor = random.choice(distractors)
            opts = [correct_emo, distractor]

        random.shuffle(opts)
        out.append({
            "id": q["id"],
            "s_type": s_type_q,
            "category": guess_category(q) if s_type_q != "情境" else None,
            "text": q.get("text", ""),
            # 舊前端只看 img → 一律回填
            "img": media_url,
            "media": media_url,
            "media_type": media_type,  # "image" / "video"
            "options": opts
        })

    session["current_questions"] = [q["id"] for q in out]
    session.modified = True
    return jsonify(out)
@bp.post("/submit_answer")
def submit_answer():
    data = request.json or {}
    qid, selected_opt = data.get("id"), data.get("selected")
    if not qid or selected_opt is None:
        return jsonify({"error": "invalid payload"}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s_id, s_type, s_text, s_answer,
                       COALESCE(s_count,0) AS s_count,
                       COALESCE(s_correct_count,0) AS s_correct_count
                  FROM System
                 WHERE s_id=%s
            """, (qid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404

            db_ans = normalize_tf(normalize_emotion(row["s_answer"]))
            sel    = normalize_tf(normalize_emotion(str(selected_opt)))
            is_correct = (sel == db_ans)

            # ★ 判斷是否為情境題（只依答案 正確/錯誤）
            is_situation_q = db_ans in ("正確", "錯誤")

            # 更新 System 統計（保留原樣）
            cur.execute("""
UPDATE System
   SET s_count = s_count + 1,
       s_correct_count = s_correct_count + %s,
       s_correct_rate =
         CAST(s_correct_count + %s AS DECIMAL(9,4)) /
         NULLIF(CAST(s_count + 1 AS DECIMAL(9,4)), 0)
 WHERE s_id = %s
""", (
    1 if is_correct else 0,
    1 if is_correct else 0,
    qid
))
        conn.commit()

    # ============================
    # ⭐ OpenAI feedback（正確的題型資訊）
    # ============================
    ai_text = generate_feedback_ai(
        is_correct,
        s_type = "情境" if is_situation_q else "情緒",
        question_text=row.get("s_text") or "",
        correct_answer=row.get("s_answer"),
        selected_answer=str(selected_opt),
    )

    final_fb = ai_text if (ai_text and ai_text.strip()) else generate_feedback(is_correct)

    # 寫 session（保留）
    answers = [a for a in session.get("answers", []) if a["s_id"] != int(qid)]
    answers.append({"s_id": int(qid), "user_answer": str(selected_opt), "is_correct": 1 if is_correct else 0})
    session["answers"] = answers
    session.modified = True

    return jsonify({
        "correct": is_correct,
        "correct_answer": row["s_answer"],
        "feedback": final_fb
    })
# --- 保留：不要在這裡掛任何路由裝飾器 ---
def _resolve_user_id_or_fallback():
    uid = (
        session.get("auth_user_id")
        or session.get("user_id")
        or session.get("uid")
        or session.get("quiz_user_id")
    )
    try:
        uid = int(uid)
    except Exception:
        uid = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            if uid is not None:
                cur.execute("SELECT user_id FROM User WHERE user_id=%s", (uid,))
                row = cur.fetchone()
                if row:
                    return row["user_id"]

            cur.execute("SELECT user_id FROM User ORDER BY user_id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                return row["user_id"]
            return None

# --- 這個才是對外 API ---
@bp.post("/end_session")
def end_session():
    try:
        if "quiz_start_time" not in session:
            return jsonify({"error": "no active session"}), 400

        user_id = _resolve_user_id_or_fallback()
        if user_id is None:
            return jsonify({"error": "no valid user to attach session"}), 500

        start_time = datetime.datetime.fromisoformat(session["quiz_start_time"])
        end_time   = datetime.datetime.now()

        all_qids = session.get("current_questions", []) or []
        answers  = session.get("answers", []) or []
        answered_qids = {a["s_id"] for a in answers}
        for qid in all_qids:
            if qid not in answered_qids:
                answers.append({"s_id": int(qid), "user_answer": "未作答", "is_correct": 0})

        correct_count = sum(a["is_correct"] for a in answers)
        total = len(all_qids) or len(answers)
        rate = round(correct_count / total, 2) if total else 0.0

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO SystemSession (user_id, start_time, end_time, correct_count, correct_rate)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, start_time, end_time, correct_count, rate))
                ss_id = cur.lastrowid

                if answers:
                    cur.executemany("""
                        INSERT INTO SystemRecord (ss_id, s_id, user_answer, is_correct)
                        VALUES (%s, %s, %s, %s)
                    """, [(ss_id, a["s_id"], a["user_answer"], a["is_correct"]) for a in answers])

                if all_qids:
                    cur.executemany(
                        "UPDATE System SET last_used_round=%s WHERE s_id=%s",
                        [(ss_id, int(qid)) for qid in all_qids]
                    )
            conn.commit()

        for k in ("quiz_user_id", "quiz_start_time", "answers", "current_questions"):
            session.pop(k, None)
        session.modified = True

        return jsonify({
            "status": "saved",
            "ss_id": ss_id,
            "user_id": user_id,
            "home_url": url_for("auth.home")
        })

    except Exception as e:
        from flask import current_app
        current_app.logger.exception("end_session failed: %s", e)
        return jsonify({"error": "end_session failed", "detail": str(e)}), 500

@bp.get("/healthz")
def healthz():
    return "OK", 200

_ensure_files()
