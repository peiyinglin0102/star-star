# services/ai/custom/routes.py
import os, sys, time, logging, re, hashlib, shutil, datetime
from pathlib import Path
from flask import jsonify, request, render_template, send_from_directory, session, redirect, url_for
from werkzeug.utils import secure_filename
from . import bp

# ---- 路徑 ----
HERE = Path(__file__).resolve().parent        # services/ai/custom
AI_DIR = HERE.parent                          # services/ai
SERV   = AI_DIR.parent                        # services/
UPLOADS = AI_DIR / "uploads"                  # 共用上傳根目錄
TMP_DIR = UPLOADS / "tmp"                     # 暫存
PERM_DIR = UPLOADS / "perm"                   # 永存
for d in (UPLOADS, TMP_DIR, PERM_DIR):
    d.mkdir(parents=True, exist_ok=True)

# 讓 Python 找到 ai/ 下的模組
for p in {str(AI_DIR), str(SERV), str(SERV.parent)}:
    if p not in sys.path:
        sys.path.append(p)

# ---- 共用工具（注意：一定要在上面的 sys.path 之後）----
from ai.predict_emotion import predict_emotion
try:
    from ai.utils_text import to_zh, gen_question_text
except Exception:
    _MAP = {"angry":"生氣","disgust":"厭惡","fear":"害怕","happy":"開心","sad":"難過","surprise":"驚訝"}
    def to_zh(label:str)->str: return _MAP.get((label or "").lower(), "")
    def gen_question_text(emotion_en: str, meta: dict=None): return "這張圖片的人看起來是什麼情緒？"

from ai.custom.db_config import get_conn   # ← 一定要在 except 外面

log = logging.getLogger(__name__)

# ---- 資料表名稱（統一管理）----
TABLE_SET     = "CustomSet"
TABLE_Q       = "Custom"
TABLE_SESSION = "CustomSession"
TABLE_RECORD  = "CustomRecord"

# ---- 上傳限制 ----
ALLOWED_IMAGE = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
MAX_FILES = 10

# 六大情緒（中文定稿）
EMO_ZH = {"生氣","厭惡","害怕","開心","難過","驚訝"}

# 英文/同義詞 → 六大情緒（中文）
_EMO_CANON_EN = {
    "angry":"生氣","anger":"生氣","mad":"生氣","annoyed":"生氣","irritated":"生氣","rage":"生氣",
    "disgust":"厭惡","disgusted":"厭惡","grossedout":"厭惡","nauseated":"厭惡",
    "fear":"害怕","scared":"害怕","afraid":"害怕","fearful":"害怕","terrified":"害怕","frightened":"害怕",
    "happy":"開心","happiness":"開心","joy":"開心","joyful":"開心","smile":"開心","cheerful":"開心","glad":"開心","delight":"開心",
    "sad":"難過","sadness":"難過","unhappy":"難過","depressed":"難過","blue":"難過","down":"難過","sorrow":"難過",
    "surprise":"驚訝","surprised":"驚訝","astonished":"驚訝","amazed":"驚訝","startled":"驚訝",
    "neutral":None,"contempt":None,"calm":None
}

# 中文同義詞 → 六大情緒（中文）
_EMO_CANON_ZH = {
    "快樂":"開心","高興":"開心","喜悅":"開心",
    "悲傷":"難過","傷心":"難過","憂鬱":"難過",
    "憤怒":"生氣","發怒":"生氣","生氣":"生氣",
    "恐懼":"害怕","畏懼":"害怕","害怕":"害怕",
    "噁心":"厭惡","噁心/厭惡":"厭惡","作嘔":"厭惡","厭惡":"厭惡",
    "驚喜":"驚訝","驚訝":"驚訝"
}
def _normalize_media_public(url: str) -> str:
    """
    把任何型態的 media_url（含網域、查詢參數、#）正規化成
    /ai/custom/uploads/<rel> 的「唯一標準形式」。
    """
    s = (url or "").strip()
    if not s:
        return ""
    # 去掉查詢參數與 fragment
    s = s.split("?", 1)[0].split("#", 1)[0]

    # 找到我們的上傳前綴（不論是否帶網域）
    marker = "/ai/custom/uploads/"
    if marker in s:
        # 只取 marker 之後的相對路徑
        rel = s.split(marker, 1)[1].lstrip("/")
        return _public_url_from_rel(rel)

    # 若沒有 marker，就當成可能已是相對路徑
    rel = _rel_from_public_url(s)
    return _public_url_from_rel(rel)

def _canon_any_to_zh(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    if s in EMO_ZH:
        return s
    s_zh = _EMO_CANON_ZH.get(s)
    if s_zh:
        return s_zh
    k = re.sub(r"[^a-z]", "", s.lower())
    if not k:
        return ""
    try:
        z = to_zh(k) or ""
        if z in EMO_ZH:
            return z
    except Exception:
        pass
    z2 = _EMO_CANON_EN.get(k)
    if z2 in EMO_ZH:
        return z2 or ""
    return ""

def _safe_tmp_filename(name: str) -> str:
    base = secure_filename(name) or "image"
    ts = int(time.time() * 1000)
    root, ext = os.path.splitext(base)
    return f"{root}_{ts}{ext or '.jpg'}"

def _allowed(name: str) -> bool:
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE

def _sha256_of(path: Path, bufsize: int = 1024*1024) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b: break
            h.update(b)
    return h.hexdigest()

def _public_url_from_rel(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/").lstrip("/")
    return f"/ai/custom/uploads/{rel}"

def _rel_from_public_url(url: str) -> str:
    pfx = "/ai/custom/uploads/"
    if url.startswith(pfx):
        return url[len(pfx):]
    return url.lstrip("/")

def _promote_to_perm_if_needed(public_url: str) -> str:
    if not public_url:
        return ""
    rel = _rel_from_public_url(public_url)
    if rel.startswith("perm/"):
        return public_url
    if not rel.startswith("tmp/"):
        return public_url
    src = UPLOADS / rel
    if not src.exists():
        return public_url
    sha = _sha256_of(src)
    _, ext = os.path.splitext(src.name)
    ext = (ext or ".bin").lower()
    dst = PERM_DIR / f"{sha}{ext}"
    if dst.exists():
        try: src.unlink(missing_ok=True)
        except Exception: pass
    else:
        shutil.move(str(src), str(dst))
    return _public_url_from_rel(f"perm/{dst.name}")

# ---- 目前登入者（有 session 就用 session）----
def _current_user_id():
    uid = request.args.get("user_id") or request.headers.get("X-USER-ID")
    if not uid and request.is_json:
        uid = (request.get_json(silent=True) or {}).get("user_id")
    try:
        return int(uid) if uid else None
    except:
        return None

def _current_user_id_from_session():
    uid = session.get('auth_user_id') or session.get('user_id')
    if uid is not None:
        try:
            return int(uid)
        except:
            pass
    return _current_user_id()

# ===================== 頁面 =====================
def _login_required(view):
    def wrap(*a, **kw):
        if not (session.get('auth_user_id') or session.get('user_id')):
            return redirect(url_for('auth.login_page'))
        return view(*a, **kw)
    wrap.__name__ = view.__name__
    return wrap

@bp.get("/")
@_login_required
def home():
    return render_template("USER/Customize.html")

@bp.get("/add")
@_login_required
def page_add():
    return render_template("USER/Add_Question.html")

@bp.get("/list")
@_login_required
def page_list():
    return render_template("USER/Quiz_list.html")

@bp.get("/play/<int:set_id>")
@_login_required
def page_play(set_id: int):
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s", (set_id, user_id))
            if not cur.fetchone():
                return redirect(url_for('custom.page_list'))
    except Exception:
        return redirect(url_for('custom.page_list'))
    finally:
        conn.close()
    return render_template("USER/Play.html", set_id=set_id)

# 靜態上傳檔案直連（支援 tmp/ 與 perm/）
@bp.get("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(str(UPLOADS), filename, as_attachment=False)

# ===================== AI 辨識（多檔） =====================
@bp.post("/api/recognize")
def api_recognize():
    files = request.files.getlist("files[]")
    if not files:
        return jsonify({"error": "沒有收到圖片"}), 400
    if len(files) > MAX_FILES:
        return jsonify({"error": f"一次最多 {MAX_FILES} 張"}), 400
    for f in files:
        if not _allowed(f.filename or ""):
            return jsonify({"error": f"不支援的檔案：{f.filename}"}), 400

    out = []
    for f in files:
        filename = _safe_tmp_filename(f.filename)
        tmp_path = TMP_DIR / filename
        f.save(str(tmp_path))
        media_url = _public_url_from_rel(f"tmp/{filename}")
        log.info("custom saved tmp: %s", tmp_path)

        ai = predict_emotion(str(tmp_path))
        if isinstance(ai, dict) and "error" in ai:
            out.append({
                "media_url": media_url,
                "answer": "",
                "answer_zh": "",
                "raw_label": "",
                "question_text": "（這張圖片的人看起來是什麼情緒？）",
                "warnings": [ai["error"]],
            })
            continue

        label_en = (ai.get("label") or "").lower()
        ans_zh = _canon_any_to_zh(label_en)
        qtext = gen_question_text(label_en, meta={"s_type": "圖片"})
        out.append({
            "media_url": media_url,          # 先給 tmp，儲存時會升級到 perm
            "answer": ans_zh,
            "answer_zh": ans_zh,
            "raw_label": label_en,
            "question_text": qtext or "這張圖片的人看起來是什麼情緒？",
        })
    return jsonify(out)

# ===================== DB 工具 =====================
def _row_to_dict(row, fields):
    if isinstance(row, dict):
        return row
    return {fields[i]: row[i] for i in range(len(fields))}

def _user_owns_set(conn, set_id: int, user_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"SELECT 1 FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s", (set_id, user_id))
        return bool(cur.fetchone())

def _session_and_set(conn, cs_id: int):
    """回傳 (set_id, owner_user_id)，便於權限檢查"""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT cs.set_id, s.user_id
            FROM `{TABLE_SESSION}` cs
            JOIN `{TABLE_SET}`     s ON s.set_id = cs.set_id
            WHERE cs.cs_id=%s
        """, (cs_id,))
        row = cur.fetchone()
    if not row:
        return (None, None)
    if isinstance(row, dict):
        return row.get("set_id"), row.get("user_id")
    return row[0], row[1]

def _question_set_and_owner(conn, c_id: int):
    """回傳 (set_id, owner_user_id) 透過題目找擁有者（刪題/改題會用到）"""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT q.set_id, s.user_id
            FROM `{TABLE_Q}` q
            JOIN `{TABLE_SET}` s ON s.set_id = q.set_id
            WHERE q.c_id=%s
        """, (c_id,))
        row = cur.fetchone()
    if not row:
        return (None, None)
    if isinstance(row, dict):
        return row.get("set_id"), row.get("user_id")
    return row[0], row[1]

# ===================== API：題庫 CRUD =====================
@bp.get("/api/sets")
def api_sets():
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT set_id, set_title, set_count, user_id
                FROM `{TABLE_SET}`
                WHERE user_id=%s
                ORDER BY set_id DESC
            """, (user_id,))
            rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(_row_to_dict(r, ["set_id","set_title","set_count","user_id"]))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.get("/api/set/<int:set_id>")
def api_set_meta(set_id: int):
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT set_id, set_title, set_count, user_id
                FROM `{TABLE_SET}`
                WHERE set_id=%s AND user_id=%s
            """, (set_id, user_id))
            r = cur.fetchone()
        if not r:
            return jsonify({"error": "題庫不存在或無權限"}), 404
        return jsonify(_row_to_dict(r, ["set_id","set_title","set_count","user_id"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.get("/api/set/<int:set_id>/questions")
def api_set_questions(set_id: int):
    user_id = _current_user_id_from_session()
    shuffle_flag = request.args.get("shuffle") in ("1", "true", "yes")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 先確認擁有權
            cur.execute(f"SELECT 1 FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s", (set_id, user_id))
            if not cur.fetchone():
                return jsonify({"error": "題庫不存在或無權限"}), 404

            if shuffle_flag:
                cur.execute(f"""
                    SELECT c_id, c_text, c_answer, c_media_url
                    FROM `{TABLE_Q}`
                    WHERE set_id=%s
                    ORDER BY RAND()
                """, (set_id,))
            else:
                cur.execute(f"""
                    SELECT c_id, c_text, c_answer, c_media_url
                    FROM `{TABLE_Q}`
                    WHERE set_id=%s
                    ORDER BY c_id ASC
                """, (set_id,))
            rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(_row_to_dict(r, ["c_id","c_text","c_answer","c_media_url"]))
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
@bp.post("/api/save_set")
def api_save_set():
    """
    建立/更新題組。預設為「PATCH」（不刪舊題），若要完整取代請在 body 傳 replace=true。
    body: {
      set_id?, title, items: [{c_id?, c_text, c_answer, c_media_url}], replace?
    }
    """
    data = request.get_json(silent=True) or {}
    user_id = _current_user_id_from_session()
    replace_flag = bool(data.get("replace") in (True, "1", "true", 1))
    # 兼容前端 payload（你目前送 prune_missing / deleted_ids）
    prune_missing = bool(data.get("prune_missing") in (True, "1", "true", 1))
    deleted_ids   = [ int(x) for x in (data.get("deleted_ids") or []) if str(x).isdigit() ]

    # 若前端用 prune_missing，視同 replace
    if prune_missing and not replace_flag:
        replace_flag = True

    try:
        set_id = int(data.get("set_id") or 0)
    except:
        set_id = 0

    title = (data.get("title") or "").strip() or time.strftime("我的題組 %Y%m%d-%H%M%S")
    items = data.get("items") or []
    

    # 基本檢查
    if not items:
        return jsonify({"error": "沒有任何題目"}), 400
    if len(items) > 10:
        return jsonify({"error": "每組最多 10 題"}), 400

    # 預處理（升級 tmp→perm、答案規範化）
    q_list = []
    for idx, it in enumerate(items, start=1):
        media = (it.get("media_url") or "").strip()
        if not media:
            return jsonify({"error": f"第 {idx} 題缺少圖片連結（請先進行 AI 辨識）"}), 400
        media_final = _promote_to_perm_if_needed(media)  # 這一步讓同檔案得到固定的 perm/sha 路徑
        media_final = _normalize_media_public(media_final)  # ← 新增這行！
        cand = (it.get("answer") or it.get("answer_zh") or it.get("raw_label") or
                it.get("label") or it.get("emotion") or "")
        ans_zh = _canon_any_to_zh(cand)
        if not ans_zh:
            return jsonify({"error": f"第 {idx} 題未選答案，或答案不在六大情緒"}), 400

        qtext = (it.get("question_text") or "這張圖片的人看起來是什麼情緒？").strip()

        q_list.append({
            "c_id": int(it.get("c_id") or 0),
            "c_text": qtext,
            "c_answer": ans_zh,
            "c_media_url": media_final
        })
    # --- 以 media_url 去重：同一張圖只留最後一筆（最後為主）
    dedup = {}
    for it in q_list:
        dedup[it["c_media_url"]] = it
    q_list = list(dedup.values())

    conn = get_conn()
    try:
        final_items = []

        with conn.cursor() as cur:
            # === A) 建/改 題組標題 + 權限驗證 ===
            if set_id:
                cur.execute(f"SELECT set_id FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s",
                            (set_id, user_id))
                if not cur.fetchone():
                    return jsonify({"error": "題庫不存在或無權限"}), 404
                cur.execute(f"UPDATE `{TABLE_SET}` SET set_title=%s WHERE set_id=%s AND user_id=%s",
                            (title, set_id, user_id))
            else:
                cur.execute(
                    f"INSERT INTO `{TABLE_SET}` (set_title, set_count, user_id) VALUES (%s, 0, %s)",
                    (title, user_id)
                )
                try:
                    set_id = int(cur.lastrowid)
                except Exception:
                    cur.execute(f"""
                        SELECT set_id FROM `{TABLE_SET}`
                        WHERE user_id=%s AND set_title=%s
                        ORDER BY set_id DESC LIMIT 1
                    """, (user_id, title))
                    row = cur.fetchone()
                    set_id = int(row["set_id"] if isinstance(row, dict) else row[0])

            # 取現有題目（含 media_url），建立「ID 與 媒體路徑」雙索引
            cur.execute(f"SELECT c_id, c_answer, c_media_url FROM `{TABLE_Q}` WHERE set_id=%s", (set_id,))
            rows_existing = cur.fetchall() or []
            old_ids = set()
            old_ans_map = {}
            old_media_map = {}
            for rr in rows_existing:
                if isinstance(rr, dict):
                    cid = int(rr.get("c_id"))
                    old_ids.add(cid)
                    old_ans_map[cid] = (rr.get("c_answer") or "")
                    murl = _normalize_media_public(rr.get("c_media_url") or "")
                else:
                    cid = int(rr[0]); old_ids.add(cid); old_ans_map[cid] = (rr[1] or "")
                    murl = _normalize_media_public(rr[2] or "")
                if murl:
                    old_media_map[murl] = cid


            keep_ids = set()
            changed_answer_cids = set()

            # === B) 逐題 upsert ===
            for it in q_list:
                c_id   = int(it["c_id"] or 0)
                qtext  = it["c_text"]
                ans_zh = it["c_answer"]
                media_final = it["c_media_url"]

                # ① 有 c_id 且屬於本 set → 更新
                if c_id and (c_id in old_ids):
                    old_ans = _canon_any_to_zh(old_ans_map.get(c_id) or "")

                    # 更新 Custom
                    cur.execute(
                        f"UPDATE `{TABLE_Q}` SET c_text=%s, c_answer=%s, c_media_url=%s, deleted_at=NULL WHERE c_id=%s",
                        (qtext, ans_zh, media_final, c_id)
                    )
                    # 同步 snapshot
                    cur.execute(
                        f"""
                        UPDATE `{TABLE_RECORD}`
                        SET
                          c_text_snapshot=%s,
                          c_media_url_snapshot=%s,
                          c_answer_snapshot=%s
                        WHERE c_id=%s
                        """,
                        (qtext, media_final, ans_zh, c_id)
                    )
                    if old_ans != ans_zh:
                        cur.execute(
                            f"""
                            UPDATE `{TABLE_RECORD}`
                            SET
                              cr_is_correct =
                                CASE
                                  WHEN %s <> '' AND
                                       LOWER(REPLACE(REPLACE(cr_user_answer,'　',''),' ','')) =
                                       LOWER(REPLACE(REPLACE(%s,'　',''),' ','')) THEN 1
                                  ELSE 0
                                END
                            WHERE c_id=%s
                            """,
                            (ans_zh, ans_zh, c_id)
                        )
                        changed_answer_cids.add(c_id)

                    keep_ids.add(c_id)
                    final_items.append({
                        "c_id": c_id, "c_text": qtext, "c_answer": ans_zh, "c_media_url": media_final
                    })
                    continue

                # ② 沒有有效 c_id：用 media_url 在同題組查既有題 → 若找到則改成「更新」
                cid_by_media = old_media_map.get(media_final)
                if cid_by_media:
                    old_ans = _canon_any_to_zh(old_ans_map.get(cid_by_media) or "")

                    cur.execute(
                        f"UPDATE `{TABLE_Q}` SET c_text=%s, c_answer=%s, c_media_url=%s, deleted_at=NULL WHERE c_id=%s",
                        (qtext, ans_zh, media_final, cid_by_media)
                    )
                    cur.execute(
                        f"""
                        UPDATE `{TABLE_RECORD}`
                        SET
                          c_text_snapshot=%s,
                          c_media_url_snapshot=%s,
                          c_answer_snapshot=%s
                        WHERE c_id=%s
                        """,
                        (qtext, media_final, ans_zh, cid_by_media)
                    )
                    if old_ans != ans_zh:
                        cur.execute(
                            f"""
                            UPDATE `{TABLE_RECORD}`
                            SET
                              cr_is_correct =
                                CASE
                                  WHEN %s <> '' AND
                                       LOWER(REPLACE(REPLACE(cr_user_answer,'　',''),' ','')) =
                                       LOWER(REPLACE(REPLACE(%s,'　',''),' ','')) THEN 1
                                  ELSE 0
                                END
                            WHERE c_id=%s
                            """,
                            (ans_zh, ans_zh, cid_by_media)
                        )
                        changed_answer_cids.add(cid_by_media)

                    keep_ids.add(cid_by_media)
                    final_items.append({
                        "c_id": cid_by_media, "c_text": qtext, "c_answer": ans_zh, "c_media_url": media_final
                    })
                    continue
                                # ③ 真的全新 → 插入  …… 之前先加保險查
                cur.execute(
                    f"SELECT c_id, c_answer FROM `{TABLE_Q}` WHERE set_id=%s AND c_media_url=%s LIMIT 1",
                    (set_id, media_final)
                )
                row_same = cur.fetchone()
                if row_same:
                    # 轉成更新流程
                    if isinstance(row_same, dict):
                        cid_by_media = int(row_same.get("c_id"))
                        old_ans = _canon_any_to_zh(row_same.get("c_answer") or "")
                    else:
                        cid_by_media = int(row_same[0])
                        old_ans = _canon_any_to_zh(row_same[1] or "")

                    cur.execute(
                        f"UPDATE `{TABLE_Q}` SET c_text=%s, c_answer=%s, c_media_url=%s, deleted_at=NULL WHERE c_id=%s",
                        (qtext, ans_zh, media_final, cid_by_media)
                    )
                    cur.execute(
                        f"""UPDATE `{TABLE_RECORD}`
                            SET c_text_snapshot=%s, c_media_url_snapshot=%s, c_answer_snapshot=%s
                            WHERE c_id=%s
                        """,
                        (qtext, media_final, ans_zh, cid_by_media)
                    )
                    if old_ans != ans_zh:
                        cur.execute(
                            f"""UPDATE `{TABLE_RECORD}`
                                SET cr_is_correct =
                                    CASE
                                      WHEN %s <> '' AND
                                           LOWER(REPLACE(REPLACE(cr_user_answer,'　',''),' ','')) =
                                           LOWER(REPLACE(REPLACE(%s,'　',''),' ','')) THEN 1
                                      ELSE 0
                                    END
                                WHERE c_id=%s
                            """,
                            (ans_zh, ans_zh, cid_by_media)
                        )
                        changed_answer_cids.add(cid_by_media)

                    keep_ids.add(cid_by_media)
                    final_items.append({
                        "c_id": cid_by_media, "c_text": qtext, "c_answer": ans_zh, "c_media_url": media_final
                    })
                    continue

                # ③ 真的全新 → UPSERT（依唯一鍵 set_id + c_media_url）
                cur.execute(
                    f"""
                    INSERT INTO `{TABLE_Q}` (set_id, c_text, c_answer, c_media_url)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                    c_text=VALUES(c_text),
                    c_answer=VALUES(c_answer),
                    c_media_url=VALUES(c_media_url),
                    deleted_at=NULL
                    """,
                    (set_id, qtext, ans_zh, media_final)
                )
                # 拿到 c_id：若是插入，拿 lastrowid；若是撞唯一鍵更新，取舊 c_id
                cur.execute(
                    f"SELECT c_id FROM `{TABLE_Q}` WHERE set_id=%s AND c_media_url=%s LIMIT 1",
                    (set_id, media_final)
                )
                row = cur.fetchone()
                new_qid = int(row["c_id"] if isinstance(row, dict) else row[0])

                # 同步索引，避免同批次後面再撞到
                old_ids.add(new_qid)
                old_ans_map[new_qid] = ans_zh
                if media_final:
                    old_media_map[media_final] = new_qid

                keep_ids.add(new_qid)
                final_items.append({
                    "c_id": new_qid, "c_text": qtext, "c_answer": ans_zh, "c_media_url": media_final
                })

                # === C) 只有 replace_flag=True 才刪除本次未保留的舊題 ===
                to_delete = []
                if replace_flag:
                    to_delete = list(old_ids - keep_ids)
                    if to_delete:
                        fmt = ",".join(["%s"] * len(to_delete))
                        cur.execute(f"DELETE FROM `{TABLE_RECORD}` WHERE c_id IN ({fmt})", tuple(to_delete))
                        cur.execute(f"DELETE FROM `{TABLE_Q}`      WHERE c_id IN ({fmt})", tuple(to_delete))

                # 額外：若前端送了 deleted_ids，強制刪除它們（不論 replace_flag）
                if deleted_ids:
                    fmt = ",".join(["%s"] * len(deleted_ids))
                    with conn.cursor() as cur:
                        cur.execute(f"DELETE FROM `{TABLE_RECORD}` WHERE c_id IN ({fmt})", tuple(deleted_ids))
                        cur.execute(f"DELETE FROM `{TABLE_Q}`      WHERE c_id IN ({fmt})", tuple(deleted_ids))


        # === 重算規則 ===
        if replace_flag and to_delete:
            _recalc_sessions_for_set(conn, set_id)
        elif changed_answer_cids:
            _recalc_sessions_for_cids(conn, list(changed_answer_cids))
        # 標題/文字/圖片變更 → 不重算

        conn.commit()
        return jsonify({
            "ok": True,
            "set_id": int(set_id),
            "saved": len(final_items),
            "mode": ("replace" if replace_flag else "patch"),
            "items": final_items
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


def _recalc_sessions_for_csids(conn, cs_ids: list[int]):
    if not cs_ids:
        return
    with conn.cursor() as cur:
        fmt = ",".join(["%s"] * len(cs_ids))
        cur.execute(f"""
            SELECT cs_id,
                   COUNT(*) AS total_cnt,
                   SUM(CASE WHEN cr_is_correct=1 THEN 1 ELSE 0 END) AS correct_cnt
            FROM `{TABLE_RECORD}`
            WHERE cs_id IN ({fmt})
            GROUP BY cs_id
        """, tuple(cs_ids))
        stats = cur.fetchall() or []

        # 只更新「真的有紀錄」的 cs_id；沒有紀錄者完全不動它，避免看起來像被歸零
        for r in stats:
            if isinstance(r, dict):
                cs_id = int(r.get("cs_id"))
                total = int(r.get("total_cnt") or 0)
                corr  = int(r.get("correct_cnt") or 0)
            else:
                cs_id, total, corr = r
                total = int(total or 0); corr = int(corr or 0)
            rate = (float(corr) / float(total)) if total > 0 else 0.0
            cur.execute(
                f"UPDATE `{TABLE_SESSION}` SET cs_correct_count=%s, cs_correct_rate=%s WHERE cs_id=%s",
                (corr, rate, cs_id)
            )

def _recalc_sessions_for_cids(conn, c_ids: list[int]):
    """找出「曾作答過這些題目」的所有 cs_id，並重算它們的統計。"""
    if not c_ids:
        return
    with conn.cursor() as cur:
        fmt = ",".join(["%s"] * len(c_ids))
        cur.execute(f"SELECT DISTINCT cs_id FROM `{TABLE_RECORD}` WHERE c_id IN ({fmt})", tuple(c_ids))
        rows = cur.fetchall() or []
        cs_ids = [ (r["cs_id"] if isinstance(r, dict) else r[0]) for r in rows ]
    _recalc_sessions_for_csids(conn, [int(x) for x in cs_ids])

def _recalc_sessions_for_set(conn, set_id: int):
    """重算某題組下『所有回合』的統計。適用於整組儲存後（可能有刪題）。"""
    with conn.cursor() as cur:
        cur.execute(f"SELECT cs_id FROM `{TABLE_SESSION}` WHERE set_id=%s", (set_id,))
        rows = cur.fetchall() or []
        cs_ids = [ (r["cs_id"] if isinstance(r, dict) else r[0]) for r in rows ]
    _recalc_sessions_for_csids(conn, [int(x) for x in cs_ids])

@bp.delete("/api/set/<int:set_id>")
def api_delete_set(set_id: int):
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 確認擁有權
            cur.execute(f"SELECT 1 FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s", (set_id, user_id))
            if not cur.fetchone():
                return jsonify({"error": "題庫不存在或無權限"}), 404

            # 先刪除該 set 內所有題目的紀錄
            cur.execute(f"SELECT c_id FROM `{TABLE_Q}` WHERE set_id=%s", (set_id,))
            cids = [ (r["c_id"] if isinstance(r, dict) else r[0]) for r in (cur.fetchall() or []) ]
            if cids:
                fmt = ",".join(["%s"]*len(cids))
                cur.execute(f"DELETE FROM `{TABLE_RECORD}` WHERE c_id IN ({fmt})", tuple(cids))

            cur.execute(f"DELETE FROM `{TABLE_Q}` WHERE set_id=%s", (set_id,))
            cur.execute(f"DELETE FROM `{TABLE_SET}` WHERE set_id=%s AND user_id=%s", (set_id, user_id))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ========== ★★ MOD：新增—更新題組標題 ==========
@bp.post("/api/set/update_title")
def api_update_set_title():
    """
    body: { set_id, new_title }
    只更新 CustomSet.set_title，其他表以 join 顯示最新標題即可
    """
    data = request.get_json(silent=True) or {}
    try:
        set_id = int(data.get("set_id") or 0)
    except:
        set_id = 0
    new_title = (data.get("new_title") or "").strip()
    if not set_id or not new_title:
        return jsonify({"error":"缺 set_id 或 new_title"}), 400

    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        if not _user_owns_set(conn, set_id, user_id):
            return jsonify({"error":"題庫不存在或無權限"}), 403
        with conn.cursor() as cur:
            cur.execute(f"UPDATE `{TABLE_SET}` SET set_title=%s WHERE set_id=%s AND user_id=%s",
                        (new_title, set_id, user_id))
        conn.commit()
        return jsonify({"ok":True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error":str(e)}), 500
    finally:
        conn.close()

# ========== ★★ MOD：新增—刪除單一題目（含歷程） ==========
@bp.delete("/api/question/<int:c_id>")
def api_delete_question(c_id: int):
    """
    刪除單題：
    1) 驗權（題目 → set → user）
    2) 先刪 CustomRecord 中該題的所有紀錄
    3) 再刪 Custom 該題
    """
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        set_id, owner_uid = _question_set_and_owner(conn, c_id)
        if not set_id:
            return jsonify({"error":"找不到題目"}), 404
        if owner_uid != user_id:
            return jsonify({"error":"無權限"}), 403

        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM `{TABLE_RECORD}` WHERE c_id=%s", (c_id,))
            cur.execute(f"DELETE FROM `{TABLE_Q}` WHERE c_id=%s", (c_id,))

        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.get("/api/set/<int:set_id>/longterm")
def api_set_longterm(set_id: int):
    """
    長期題目表現（跨所有回合）
    - 每一題一定會出現（即使尚無任何作答）
    回傳：[{ c_id,c_text,c_answer,c_media_url, answered_cnt, correct_cnt, acc_pct }]
    """
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        # 權限
        if not _user_owns_set(conn, set_id, user_id):
            return jsonify({"error": "題庫不存在或無權限"}), 403

        with conn.cursor() as cur:
            # 重要：LEFT JOIN，確保沒作答也會列出（answered_cnt=0）
            cur.execute(f"""
                SELECT
                  q.c_id,
                  q.c_text,
                  q.c_answer,
                  q.c_media_url,
                  COUNT(r.cr_id) AS answered_cnt,
                  SUM(CASE WHEN r.cr_is_correct=1 THEN 1 ELSE 0 END) AS correct_cnt
                FROM `{TABLE_Q}` q
                LEFT JOIN `{TABLE_RECORD}` r
                  ON r.c_id = q.c_id
                LEFT JOIN `{TABLE_SESSION}` cs
                  ON cs.cs_id = r.cs_id
                  AND cs.set_id = q.set_id
                WHERE q.set_id=%s
                GROUP BY q.c_id, q.c_text, q.c_answer, q.c_media_url
                ORDER BY q.c_id ASC
            """, (set_id,))
            rows = cur.fetchall() or []

        out = []
        for r in rows:
            if isinstance(r, dict):
                c_id   = int(r.get("c_id"))
                c_text = r.get("c_text") or ""
                c_ans  = r.get("c_answer") or ""
                c_img  = r.get("c_media_url") or ""
                answered = int(r.get("answered_cnt") or 0)
                correct  = int(r.get("correct_cnt") or 0)
            else:
                c_id, c_text, c_ans, c_img, answered, correct = r
                answered = int(answered or 0); correct = int(correct or 0)
            acc = (float(correct)/float(answered)) if answered>0 else 0.0
            out.append({
                "c_id": c_id,
                "c_text": c_text,
                "c_answer": c_ans,
                "c_media_url": c_img,
                "answered_cnt": answered,
                "correct_cnt": correct,
                "acc_pct": round(acc, 4)
            })
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ========== ★★ MOD：新增—更新單一題目（重算答對、同步快照） ==========
@bp.post("/api/question/update")
def api_update_question():
    """
    body: { c_id, c_text?, c_answer?, c_media_url? }
    - 允許更新 c_text / c_answer / c_media_url
    - 更新 Custom
    - 將 CustomRecord 的 snapshot 同步為最新 c_* 值
    - 若 c_answer 變更：依「原 cr_user_answer」重算 cr_is_correct
    """
    data = request.get_json(silent=True) or {}
    try:
        c_id = int(data.get("c_id") or 0)
    except:
        c_id = 0
    if not c_id:
        return jsonify({"error":"缺 c_id"}), 400

    new_text  = data.get("c_text")
    # 若 body 帶了 c_answer，就規範化；否則用 None 表示「不變」
    new_ans_raw = (data.get("c_answer") if ("c_answer" in data) else None)
    new_ans_canon = (_canon_any_to_zh(new_ans_raw or "") if new_ans_raw is not None else None)
    new_media = data.get("c_media_url")

    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        set_id, owner_uid = _question_set_and_owner(conn, c_id)
        if not set_id:
            return jsonify({"error":"找不到題目"}), 404
        if owner_uid != user_id:
            return jsonify({"error":"無權限"}), 403

        # 讀舊值
        with conn.cursor() as cur:
            cur.execute(f"SELECT c_text, c_answer, c_media_url FROM `{TABLE_Q}` WHERE c_id=%s", (c_id,))
            row = cur.fetchone()
        if not row:
            return jsonify({"error":"找不到題目"}), 404

        if isinstance(row, dict):
            old_text, old_ans, old_media = row.get("c_text"), row.get("c_answer"), row.get("c_media_url")
        else:
            old_text, old_ans, old_media = row[0], row[1], row[2]
        # 讀舊值之後、準備新值之前加上：
        if new_media is not None:
            new_media = _promote_to_perm_if_needed(_normalize_media_public(new_media))

        # 準備新值
        upd_text  = (new_text  if new_text  is not None else old_text) or ""
        upd_media = (new_media if new_media is not None else old_media) or ""
        if new_ans_raw is None:
            upd_ans_canon = _canon_any_to_zh(old_ans or "")
        else:
            upd_ans_canon = new_ans_canon or ""
            if not upd_ans_canon:
                return jsonify({"error": "c_answer 不在六大情緒（生氣/厭惡/害怕/開心/難過/驚訝），拒絕寫入空答案"}), 400

        old_ans_canon = _canon_any_to_zh(old_ans or "")
        ans_changed = (old_ans_canon != upd_ans_canon)

        # 1) 更新 Custom
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE `{TABLE_Q}` SET c_text=%s, c_answer=%s, c_media_url=%s WHERE c_id=%s",
                (upd_text, upd_ans_canon, upd_media, c_id)
            )

        # 2) 同步快照：一定要做（讓前台顯示最新文字/圖片/答案）
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE `{TABLE_RECORD}`
                SET
                  c_text_snapshot=%s,
                  c_media_url_snapshot=%s,
                  c_answer_snapshot=%s
                WHERE c_id=%s
                """,
                (upd_text, upd_media, upd_ans_canon, c_id)
            )

        # 3) 只有答案改了才重算「這題」在各回合的正確與否 + 回合統計
        if ans_changed:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{TABLE_RECORD}`
                    SET
                      cr_is_correct =
                        CASE
                          WHEN %s <> '' AND
                               LOWER(REPLACE(REPLACE(cr_user_answer,'　',''),' ','')) =
                               LOWER(REPLACE(REPLACE(%s,'　',''),' ','')) THEN 1
                          ELSE 0
                        END
                    WHERE c_id=%s
                    """,
                    (upd_ans_canon, upd_ans_canon, c_id)
                )
            _recalc_sessions_for_cids(conn, [c_id])

        conn.commit()
        return jsonify({"ok":True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error":str(e)}), 500
    finally:
        conn.close()

# ===================== 作答回合（完整串 DB） =====================
@bp.get("/api/_dbdiag")
def api_dbdiag():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE(), CURRENT_USER(), USER()")
            db, curu, u = cur.fetchone()
            cur.execute("SHOW TABLES LIKE 'CustomSet'")
            has_set = bool(cur.fetchone())
            cur.execute("SELECT COUNT(*) FROM CustomSet")
            cnt = cur.fetchone()[0]
        conn.close()
        return jsonify({
            "ok": True, "db": db, "current_user": curu, "user": u,
            "has_table_CustomSet": has_set, "customset_count": int(cnt)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.post("/api/session/start")
def api_session_start():
    """
    輸入: { set_id }
    1) 確認 set 屬於目前登入使用者
    2) INSERT INTO CustomSession (set_id, cs_start_time=NOW())
    3) 回傳 cs_id
    """
    data = request.get_json(silent=True) or {}
    set_id = int(data.get("set_id") or 0)
    if not set_id:
        return jsonify({"error": "缺 set_id"}), 400

    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        if not _user_owns_set(conn, set_id, user_id):
            return jsonify({"error": "題庫不存在或無權限"}), 403

        with conn.cursor() as cur:
            cur.execute(
                 f"""INSERT INTO `{TABLE_SESSION}`
                (set_id, cs_start_time, cs_end_time, cs_correct_count, cs_correct_rate)
                VALUES (%s, NOW(), NULL, 0, 0.0)""",
                 (set_id,)
            )
            try:
                cs_id = cur.lastrowid
            except Exception:
                cur.execute(
                    f"SELECT cs_id FROM `{TABLE_SESSION}` WHERE set_id=%s ORDER BY cs_id DESC LIMIT 1",
                    (set_id,)
                )
                row = cur.fetchone()
                cs_id = (row["cs_id"] if isinstance(row, dict) else (row[0] if row else None))

        conn.commit()
        if not cs_id:
            return jsonify({"error": "無法取得回合編號"}), 500
        return jsonify({"ok": True, "cs_id": int(cs_id)})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.post("/api/answer")
def api_submit_answer():
    """
    輸入: { cs_id, c_id, user_answer }
    1) 找到 cs_id 對應的 set_id + 擁有者 user_id（驗權）
    2) 讀出 c_id 的正解與 set_id，確認同一題組
    3) 規範化後比對，寫入 CustomRecord（★★ 同步寫入快照）
    回傳: { ok, is_correct: 0/1 }
    """
    data = request.get_json(silent=True) or {}
    cs_id = int(data.get("cs_id") or 0)
    c_id  = int(data.get("c_id")  or 0)
    user_answer_raw = (data.get("user_answer") or "").strip()

    if not cs_id or not c_id:
        return jsonify({"error":"缺 cs_id 或 c_id"}), 400

    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        sess_set_id, owner_uid = _session_and_set(conn, cs_id)
        if not sess_set_id:
            return jsonify({"error":"回合不存在"}), 404
        if owner_uid != user_id:
            return jsonify({"error":"無權限"}), 403

        # 取得題目內容（用於快照）
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT set_id, c_answer, c_text, c_media_url FROM `{TABLE_Q}` WHERE c_id=%s",
                (c_id,)
            )
            row = cur.fetchone()
        if not row:
            return jsonify({"error":"找不到題目"}), 404

        if isinstance(row, dict):
            q_set_id = row.get("set_id")
            c_ans    = row.get("c_answer") or ""
            c_text   = row.get("c_text") or ""
            c_media  = row.get("c_media_url") or ""
        else:
            q_set_id = row[0]
            c_ans    = row[1] or ""
            c_text   = row[2] or ""
            c_media  = row[3] or ""

        if int(q_set_id or 0) != int(sess_set_id):
            return jsonify({"error":"題目不屬於此回合的題組"}), 400

        ua = _canon_any_to_zh(user_answer_raw)
        ca = _canon_any_to_zh(c_ans)
        is_correct = 1 if (ua and ca and ua == ca) else 0

        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO `{TABLE_RECORD}` "
                f"(cs_id, c_id, cr_user_answer, cr_is_correct, "
                f" c_answer_snapshot, c_text_snapshot, c_media_url_snapshot) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (cs_id, c_id, (ua or user_answer_raw), is_correct,
                 ca, c_text, c_media)
            )
        conn.commit()
        return jsonify({"ok": True, "is_correct": int(is_correct)})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# === 自訂題庫：頁面（保留） ===
@bp.get("/custom-sets")
def page_custom_sets():
    return render_template("ADMIN/CustomSets.html")

# === 自訂題庫：列出某用戶的題組 (CustomSet) ===
@bp.get("/api/customset/list")
def api_customset_list():
    """
    GET /ai/custom/api/customset/list?user_id=8
    回傳：[
      { set_id, set_title, set_count, correct_rate }
    ]
    correct_rate: 0.0~1.0（沒有紀錄時為 None）
    """
    try:
        user_id = int(request.args.get("user_id") or 0)
    except Exception:
        return jsonify({"error": "缺少或不合法的 user_id"}), 400
    if user_id <= 0:
        return jsonify({"error": "缺少或不合法的 user_id"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT set_id, set_title, set_count
                FROM `{TABLE_SET}`
                WHERE user_id=%s
                ORDER BY set_id DESC
            """, (user_id,))
            rows = cur.fetchall() or []

        sets = []
        set_ids = []
        for r in rows:
            if isinstance(r, dict):
                sid = r.get("set_id")
                title = r.get("set_title")
                cnt = r.get("set_count")
            else:
                sid, title, cnt = r
            obj = {
                "set_id": sid,
                "set_title": title,
                "set_count": (0 if cnt is None else cnt),
            }
            sets.append(obj)
            set_ids.append(sid)

        # === 計算各題組的正確率（CustomSession + CustomRecord 聚合） ===
        rate_map = {}
        if set_ids:
            with conn.cursor() as cur:
                fmt = ",".join(["%s"] * len(set_ids))
                cur.execute(f"""
                    SELECT cs.set_id,
                           COUNT(r.cr_id) AS total_cnt,
                           SUM(CASE WHEN r.cr_is_correct=1 THEN 1 ELSE 0 END) AS correct_cnt
                    FROM `{TABLE_SESSION}` cs
                    JOIN `{TABLE_RECORD}`  r  ON r.cs_id = cs.cs_id
                    WHERE cs.set_id IN ({fmt})
                    GROUP BY cs.set_id
                """, tuple(set_ids))
                stats = cur.fetchall() or []
                for st in stats:
                    if isinstance(st, dict):
                        sid = st.get("set_id")
                        total = int(st.get("total_cnt") or 0)
                        corr  = int(st.get("correct_cnt") or 0)
                    else:
                        sid, total, corr = st
                        total = int(total or 0); corr = int(corr or 0)
                    rate_map[sid] = (float(corr) / float(total)) if total > 0 else None

        for s in sets:
            s["correct_rate"] = rate_map.get(s["set_id"])

        return jsonify(sets)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# === 自訂題庫：列出某題組的題目 (Custom) ===
@bp.get("/api/custom/items")
def api_custom_items():
    """
    GET /ai/api/custom/items?set_id=123
    回傳：[{c_id, set_id, c_text, c_answer, c_media_url}]
    """
    try:
        set_id = int(request.args.get("set_id") or 0)
    except Exception:
        return jsonify({"error": "缺少或不合法的 set_id"}), 400
    if set_id <= 0:
        return jsonify({"error": "缺少或不合法的 set_id"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c_id, set_id, c_text, c_answer, c_media_url
                FROM `Custom`
                WHERE set_id=%s
                ORDER BY c_id ASC
            """, (set_id,))
            rows = cur.fetchall() or []

        out = []
        for r in rows:
            if isinstance(r, dict):
                out.append({
                    "c_id": r.get("c_id"),
                    "set_id": r.get("set_id"),
                    "c_text": r.get("c_text") or "",
                    "c_answer": r.get("c_answer") or "",
                    "c_media_url": r.get("c_media_url") or "",
                })
            else:
                out.append({
                    "c_id": r[0], "set_id": r[1],
                    "c_text": r[2] or "", "c_answer": r[3] or "",
                    "c_media_url": r[4] or ""
                })
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.post("/api/session/finish")
def api_session_finish():
    """
    輸入: { cs_id }
    1) 取出 set_id 與擁有者 user_id；驗權
    2) 統計 CustomRecord: 總筆數 & 正確數
    3) UPDATE CustomSession: cs_end_time, cs_correct_count, cs_correct_rate
    4) UPDATE CustomSet: set_count += 1
    """
    data = request.get_json(silent=True) or {}
    cs_id = int(data.get("cs_id") or 0)
    if not cs_id:
        return jsonify({"error":"缺 cs_id"}), 400

    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        set_id, owner_uid = _session_and_set(conn, cs_id)
        if not set_id:
            return jsonify({"error":"回合不存在"}), 404
        if owner_uid != user_id:
            return jsonify({"error":"無權限"}), 403

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN cr_is_correct=1 THEN 1 ELSE 0 END) "
                f"FROM `{TABLE_RECORD}` WHERE cs_id=%s",
                (cs_id,)
            )
            row = cur.fetchone()
            if isinstance(row, dict):
                total   = int(row.get("COUNT(*)") or 0)
                correct = int(row.get("SUM(CASE WHEN cr_is_correct=1 THEN 1 ELSE 0 END)") or 0)
            else:
                total   = int(row[0] or 0)
                correct = int(row[1] or 0)

            rate = (correct / total) if total > 0 else 0.0

            cur.execute(
                f"UPDATE `{TABLE_SESSION}` "
                f"SET cs_end_time=NOW(), cs_correct_count=%s, cs_correct_rate=%s "
                f"WHERE cs_id=%s",
                (correct, float(rate), cs_id)
            )

            cur.execute(
                f"UPDATE `{TABLE_SET}` SET set_count = COALESCE(set_count,0) + 1 WHERE set_id=%s",
                (set_id,)
            )

        conn.commit()
        return jsonify({"ok": True, "total": total, "correct": correct, "rate": rate})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@bp.get("/api/session/summary/<int:cs_id>")
def api_session_summary(cs_id: int):
    user_id = _current_user_id_from_session()
    conn = get_conn()
    try:
        set_id, owner_uid = _session_and_set(conn, cs_id)
        if not set_id:
            return jsonify({"error":"回合不存在"}), 404
        if owner_uid != user_id:
            return jsonify({"error":"無權限"}), 403

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT cs_id, set_id, cs_start_time, cs_end_time, cs_correct_count, cs_correct_rate "
                f"FROM `{TABLE_SESSION}` WHERE cs_id=%s",
                (cs_id,)
            )
            sess = cur.fetchone()

            # 取紀錄時一併拿目前題目（若題目已刪，保留 snapshot）
            cur.execute(
                f"""SELECT r.cr_id, r.c_id, r.cr_user_answer, r.cr_is_correct,
                           COALESCE(q.c_answer, r.c_answer_snapshot)       AS c_answer,
                           COALESCE(q.c_text,   r.c_text_snapshot)         AS c_text,
                           COALESCE(q.c_media_url, r.c_media_url_snapshot) AS c_media_url
                    FROM `{TABLE_RECORD}` r
                    LEFT JOIN `{TABLE_Q}` q ON q.c_id = r.c_id
                    WHERE r.cs_id=%s
                    ORDER BY r.cr_id ASC
                """,
                (cs_id,)
            )
            recs = cur.fetchall()

        def _as_dict_list(rows, fields):
            out = []
            for row in rows or []:
                if isinstance(row, dict):
                    out.append({k: row.get(k) for k in fields})
                else:
                    out.append({fields[i]: row[i] for i in range(len(fields))})
            return out

        sess_obj = None
        if sess:
            if isinstance(sess, dict):
                sess_obj = {
                    "cs_id": sess.get("cs_id"), "set_id": sess.get("set_id"),
                    "cs_start_time": sess.get("cs_start_time"), "cs_end_time": sess.get("cs_end_time"),
                    "cs_correct_count": sess.get("cs_correct_count"), "cs_correct_rate": sess.get("cs_correct_rate"),
                }
            else:
                sess_obj = {
                    "cs_id": sess[0], "set_id": sess[1], "cs_start_time": sess[2], "cs_end_time": sess[3],
                    "cs_correct_count": sess[4], "cs_correct_rate": sess[5],
                }

        rec_fields = ["cr_id","c_id","cr_user_answer","cr_is_correct","c_answer","c_text","c_media_url"]
        return jsonify({
            "session": sess_obj,
            "records": _as_dict_list(recs, rec_fields)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- 輕量健康檢查（給前端或 curl）
@bp.get("/api/ping")
def api_ping():
    return jsonify({"ok": True, "ns": "ai.custom"}), 200

# --- items 的路徑 alias，避免誤用文件中的 /ai/api/custom/items
@bp.get("/api/items")
def api_items_alias():
    return api_custom_items()

@bp.get("/api/whoami")
def api_whoami():
    return jsonify({
        "auth_user_id": session.get("auth_user_id"),
        "user_id": session.get("user_id")
    })

# ===================== 清理 tmp（手動 API；可排程） =====================
@bp.get("/api/uploads/cleanup")
def api_cleanup_tmp():
    """
    /ai/custom/api/uploads/cleanup?hours=6
    刪除 tmp/ 下最後修改時間超過 hours 小時的檔案（預設 6）
    """
    try:
        hours = float(request.args.get("hours") or 6)
    except:
        hours = 6.0
    cutoff = time.time() - hours * 3600
    removed = 0
    for p in TMP_DIR.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except Exception:
            pass
    return jsonify({"ok": True, "removed": removed, "older_than_hours": hours})
