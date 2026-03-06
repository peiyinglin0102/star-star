# services/ai/routes.py
import os, sys, time, random, logging, re, hashlib, shutil
from pathlib import Path
from flask import jsonify, request, render_template, send_from_directory
from werkzeug.utils import secure_filename
from . import bp
from .db_config import get_conn


# ===== 情緒標籤正規化：不管模型回什麼英文/同義詞，都對到 6 個固定中文字 =====
_EMO_CANON = {
   "angry": "生氣", "anger": "生氣", "mad": "生氣", "annoyed": "生氣", "irritated": "生氣",
   "disgust": "厭惡", "disgusted": "厭惡",
   "fear": "害怕", "scared": "害怕", "afraid": "害怕", "fearful": "害怕",
   "happy": "開心", "happiness": "開心", "joy": "開心", "joyful": "開心", "smile": "開心",
   "sad": "難過", "sadness": "難過", "unhappy": "難過", "depressed": "難過",
   "surprise": "驚訝", "surprised": "驚訝", "astonished": "驚訝", "amazed": "驚訝",
   "neutral": None, "contempt": None, "calm": None, "fearful": "害怕",
}
def canon_zh_from_label(label_en: str):
   if not label_en:
       return None, None
   k = re.sub(r"[^a-z]", "", str(label_en).lower())
   zh = _EMO_CANON.get(k)
   if zh:
       return k, zh
   return None, None


HERE = Path(__file__).resolve().parent       # services/ai
SERV = HERE.parent                           # services/
UPLOADS = HERE / "uploads"
TMP_DIR = UPLOADS / "tmp"
PERM_DIR = UPLOADS / "perm"
for d in (UPLOADS, TMP_DIR, PERM_DIR):
   d.mkdir(parents=True, exist_ok=True)


# 專案路徑
for p in {str(SERV), str(SERV.parent)}:
   if p not in sys.path:
       sys.path.append(p)


from utils_text import gen_question_text
from .db_config import get_conn
from predict_emotion import predict_emotion


log = logging.getLogger(__name__)


# ---- 檔案型態 ----
ALLOWED_IMAGE = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
ALLOWED_VIDEO = {"mp4", "mov", "webm", "mkv", "avi"}
MAX_FILES = 10


# 六大情緒（中文）
EMOTION_ZH = {"生氣", "厭惡", "害怕", "開心", "難過", "驚訝"}


def _safe_tmp_filename(name: str) -> str:
   base = secure_filename(name) or "file"
   ts = int(time.time() * 1000)
   root, ext = os.path.splitext(base)
   return f"{root}_{ts}{ext or '.dat'}"


def _allowed_any(name: str) -> bool:
   if "." not in name:
       return False
   ext = name.rsplit(".", 1)[1].lower()
   return ext in ALLOWED_IMAGE or ext in ALLOWED_VIDEO


def _sha256_of(path: Path, bufsize: int = 1024*1024) -> str:
   h = hashlib.sha256()
   with open(path, "rb") as f:
       while True:
           b = f.read(bufsize)
           if not b: break
           h.update(b)
   return h.hexdigest()


def _public_url_from_rel(rel_path: str) -> str:
   rel = rel_path.replace("\\", "/").lstrip("/")
   return f"/ai/uploads/{rel}"


def _rel_from_public_url(url: str) -> str:
   pfx = "/ai/uploads/"
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


# ========== 頁面 ==========
@bp.get("/health")
def health():
   return jsonify({"ok": True})


@bp.get("/")
def home():
   return render_template("ADMIN/Question.html")


@bp.get("/uploads/<path:filename>")
def uploads(filename):
   return send_from_directory(str(UPLOADS), filename, as_attachment=False)


@bp.get("/login")
def page_login():
   return render_template("ADMIN/login.html")


@bp.get("/frontpage")
def page_frontpage():
   return render_template("ADMIN/frontpage.html")


@bp.get("/user-info")
def page_userinfo():
   return render_template("ADMIN/Userinformation.html")


@bp.get("/question")
def page_question():
   return render_template("ADMIN/Question.html")


@bp.get("/picture-question")
def page_picture_question():
   return render_template("ADMIN/Picture-question.html")


@bp.get("/gif-question")
def page_gif_question():
   return render_template("ADMIN/GIF-question.html")


@bp.get("/situation-question")
def page_situation_question():
   return render_template("ADMIN/Situation-question.html")


# ========== 內部：偵測 Category 的群組欄位 ==========
_CACHED_CAT_GROUP_COL = None  # "category_group" / "`group`" / "c_group" / "grp" / None


def _detect_category_group_col(conn):
   global _CACHED_CAT_GROUP_COL
   if _CACHED_CAT_GROUP_COL is not None:
       return _CACHED_CAT_GROUP_COL
   try:
       with conn.cursor() as cur:
           cur.execute("SHOW COLUMNS FROM `Category` LIKE 'category_group'")
           if cur.fetchone():
               _CACHED_CAT_GROUP_COL = "category_group"; return _CACHED_CAT_GROUP_COL
           cur.execute("SHOW COLUMNS FROM `Category` LIKE 'group'")
           if cur.fetchone():
               _CACHED_CAT_GROUP_COL = "`group`"; return _CACHED_CAT_GROUP_COL
           cur.execute("SHOW COLUMNS FROM `Category` LIKE 'c_group'")
           if cur.fetchone():
               _CACHED_CAT_GROUP_COL = "c_group"; return _CACHED_CAT_GROUP_COL
           cur.execute("SHOW COLUMNS FROM `Category` LIKE 'grp'")
           if cur.fetchone():
               _CACHED_CAT_GROUP_COL = "grp"; return _CACHED_CAT_GROUP_COL
   except Exception as e:
       log.warning("detect category group col failed: %s", e)
   _CACHED_CAT_GROUP_COL = None
   return None


# ========== AI 辨識（圖片/動圖）==========
@bp.post("/api/recognize")
def api_recognize():
    s_type = request.form.get("s_type", "圖片")
    # 新增：可選角色與是否強制帶入
    role = (request.form.get("role") or "").strip()
    inject_role = str(request.form.get("inject_role") or "").strip().lower() in ("1", "true", "yes", "on")

    files = request.files.getlist("files[]")
    if not files:
        return jsonify({"error": "沒有收到檔案"}), 400
    if len(files) > MAX_FILES:
        return jsonify({"error": f"一次最多 {MAX_FILES} 個檔案"}), 400
    for f in files:
        fn = f.filename or ""
        if not _allowed_any(fn):
            return jsonify({"error": f"不支援的檔案：{fn}"}), 400

    results = []
    for f in files:
        filename = _safe_tmp_filename(f.filename)
        path = TMP_DIR / filename
        f.save(str(path))
        media_url = _public_url_from_rel(f"tmp/{filename}")
        log.info("saved tmp: %s", path)

        meta = {"s_type": s_type}
        if role:
            meta["role"] = role
            meta["inject_role"] = inject_role  # True→一定帶入；False→0.7機率帶入

        if s_type in ("圖片", "動圖"):
            ai = predict_emotion(str(path))
            if isinstance(ai, dict) and "error" in ai:
                safe_q = gen_question_text("unknown", meta=meta)
                results.append({
                    "media_url": media_url,
                    "question_text": safe_q,
                    "answer": "",
                    "distractor": "",
                    "confidence": 0.0,
                    "raw_label": "",
                    "warnings": [ai["error"]],
                })
                continue

            label_en = ai.get("label", "")
            raw_en, answer_zh = canon_zh_from_label(label_en)

            conf = ai.get("confidence", 0.0) or 0.0
            try:
                conf = float(conf)
            except:
                conf = 0.0
            if conf > 1.0:
                conf = conf / 100.0
            conf = max(0.0, min(1.0, conf))

            log.info("AI label=%s → canon=%s/%s, conf=%.3f", label_en, raw_en, answer_zh, conf)

            if not answer_zh:
                # label不在六情緒→保底：不自動填答案
                results.append({
                    "media_url": media_url,
                    "question_text": gen_question_text(raw_en or "unknown", meta=meta),
                    "answer": "",
                    "distractor": "",
                    "confidence": float(conf),
                    "raw_label": str(label_en),
                    "warnings": ["label not in six emotions; fallback to manual"],
                })
                continue

            question_text = gen_question_text(raw_en or label_en, meta=meta)

            results.append({
                "media_url": media_url,
                "question_text": question_text,
                "answer": answer_zh,
                "distractor": "",
                "confidence": float(conf),
                "raw_label": raw_en or label_en,
                "warnings": ai.get("warnings", []),
            })

        elif s_type == "情境":
            results.append({
                "media_url": media_url,
                "question_text": gen_question_text("unknown", meta={"s_type": s_type, "role": role, "inject_role": inject_role}),
                "answer": "",
                "distractor": "",
                "confidence": 0.0,
                "raw_label": "",
                "warnings": ["情境題暫不啟用 AI 自動產生"],
            })
        else:
            return jsonify({"error": "未知的 s_type"}), 400

    return jsonify(results)



# ========== 通用上傳（圖片/影片，存 tmp/）==========
@bp.post("/api/upload")
def api_upload():
   files = request.files.getlist("files[]")
   if not files:
       return jsonify({"error": "沒有收到檔案"}), 400
   if len(files) > MAX_FILES:
       return jsonify({"error": f"一次最多 {MAX_FILES} 個檔案"}), 400


   out = []
   for f in files:
       fn = f.filename or ""
       if not _allowed_any(fn):
           return jsonify({"error": f"不支援的檔案：{fn}"}), 400
       filename = _safe_tmp_filename(fn)
       f.save(str(TMP_DIR / filename))
       out.append({"media_url": _public_url_from_rel(f"tmp/{filename}")})
   return jsonify(out)


# ========== Category 工具與 API ==========
def _ensure_category_id(conn, c_name: str, group_name: str) -> int:
   c_name = (c_name or "").strip() or "未分類"
   group_name = (group_name or "").strip() or "misc"
   group_col = _detect_category_group_col(conn)


   with conn.cursor() as cur:
       if group_col:
           sql_sel = f"SELECT c_id FROM `Category` WHERE c_name=%s AND {group_col}=%s LIMIT 1"
           cur.execute(sql_sel, (c_name, group_name))
           row = cur.fetchone()
           if row:
               return row["c_id"] if isinstance(row, dict) else row[0]


           sql_ins = f"INSERT INTO `Category` (c_name, {group_col}) VALUES (%s, %s)"
           cur.execute(sql_ins, (c_name, group_name))
       else:
           cur.execute("SELECT c_id FROM `Category` WHERE c_name=%s LIMIT 1", (c_name,))
           row = cur.fetchone()
           if row:
               return row["c_id"] if isinstance(row, dict) else row[0]
           cur.execute("INSERT INTO `Category` (c_name) VALUES (%s)", (c_name,))
       cur.execute("SELECT LAST_INSERT_ID()")
       rid = cur.fetchone()
       return rid["LAST_INSERT_ID()"] if isinstance(rid, dict) else rid[0]

@bp.get("/api/category/list")
def api_category_list():
    g = (request.args.get("group") or "").strip().lower()
    g_map = {"situation": "situation", "情境": "situation",
             "emotion": "emotion", "情緒": "emotion"}
    group_name = g_map.get(g, "situation")

    conn = get_conn()
    try:
        group_col = _detect_category_group_col(conn)

        # 檢查是否存在 c_active 欄位（若沒有則忽略，不影響舊資料庫）
        has_c_active = False
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW COLUMNS FROM `Category` LIKE 'c_active'")
                has_c_active = bool(cur.fetchone())
        except Exception:
            has_c_active = False

        with conn.cursor() as cur:
            # 動態組 WHERE
            where_parts = []
            params = []

            if group_col:
                where_parts.append(f"{group_col}=%s")
                params.append(group_name)

            if has_c_active:
                # 只取 c_active=1（或 NULL）視為啟用
                where_parts.append("(c_active=1 OR c_active IS NULL)")

            where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

            if group_col:
                sql = f"""
                    SELECT c_id, c_name, {group_col}
                    FROM `Category`
                    {where_sql}
                    ORDER BY c_id ASC
                """
            else:
                sql = f"""
                    SELECT c_id, c_name
                    FROM `Category`
                    {where_sql}
                    ORDER BY c_id ASC
                """

            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        out = []
        for r in rows:
            if isinstance(r, dict):
                d = {"c_id": r.get("c_id"), "c_name": r.get("c_name")}
                if group_col:
                    d["group"] = r.get(group_col.strip("`"))
                out.append(d)
            else:
                if group_col:
                    out.append({"c_id": r[0], "c_name": r[1], "group": r[2]})
                else:
                    out.append({"c_id": r[0], "c_name": r[1]})

        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ========== 儲存到 System（自動決定 s_category；並升級媒體）==========
@bp.post("/api/save")
def api_save():
   """
   JSON:
   {
     "target": "system",
     "s_type": "圖片" | "動圖" | "情境",
     "admin_id": 1,
     "s_category": null,
     "s_category_name": "社交",
     "items":[
       {"question_text":"...", "answer":"開心|正確|...", "media_url":"/ai/uploads/tmp/xxx",
        "category_id": 3, "category_name":"安全"}
     ]
   }
   """
   data = request.get_json(silent=True) or {}
   target = data.get("target", "system")
   s_type = data.get("s_type") or "圖片"
   admin_id = int(data.get("admin_id") or 1)
   s_category_batch_id = data.get("s_category")
   s_category_batch_name = data.get("s_category_name")
   items = data.get("items") or []


   if target != "system":
       return jsonify({"error": "目前僅支援存到 System"}), 400
   if s_type not in ("圖片", "動圖", "情境"):
       return jsonify({"error": "s_type 不合法"}), 400
   if not items:
       return jsonify({"error": "沒有要儲存的題目"}), 400


   conn = get_conn()
   try:
       with conn.cursor() as cur:
           sql = """
           INSERT INTO `System`
             (s_type, s_category, admin_id, s_text, s_answer, s_count, s_correct_count, s_correct_rate, s_media_url, s_createdate)
           VALUES
             (%s,     %s,         %s,       %s,     %s,       0,       0,               0,              %s,        NOW())
           """


           for it in items:
               qtext = (it.get("question_text") or "").strip()
               ans   = (it.get("answer") or "").strip()
               media = (it.get("media_url") or "").strip()
               media = _promote_to_perm_if_needed(media)  # 升級 tmp → perm


               # 1) 先用 item 給的 category_id
               cat_id = None
               try:
                   if it.get("category_id") is not None:
                       cid = int(it["category_id"])
                       if cid > 0:
                           cat_id = cid
               except Exception:
                   pass


               # 2) 沒有就用整批 id
               if cat_id is None:
                   try:
                       if s_category_batch_id is not None:
                           cid = int(s_category_batch_id)
                           if cid > 0:
                               cat_id = cid
                   except Exception:
                       pass


               # 3) 自動決定
               if cat_id is None:
                   if s_type in ("圖片", "動圖"):
                       cname = ans if ans in EMOTION_ZH else "未分類"
                       cat_id = _ensure_category_id(conn, cname, "emotion")
                   else:
                       cname = (it.get("category_name") or s_category_batch_name or "未分類")
                       cat_id = _ensure_category_id(conn, cname, "situation")


               cur.execute(sql, (s_type, cat_id, admin_id, qtext, ans, media))
       conn.commit()
       return jsonify({"ok": True, "saved": len(items)})
   except Exception as e:
       conn.rollback()
       return jsonify({"error": str(e)}), 500
   finally:
       conn.close()


# ========== 管理頁列表（依題型）==========
@bp.get("/api/system/list")
def api_system_list():
   t = (request.args.get("type") or "").strip().lower()
   t_map = {"image":"圖片","gif":"動圖","situation":"情境","圖片":"圖片","動圖":"動圖","情境":"情境"}
   s_type = t_map.get(t, "圖片")


   conn = get_conn()
   try:
       with conn.cursor() as cur:
           sql = """
           SELECT s.s_id, s.s_text, s.s_answer, s.s_media_url, s.s_createdate,
                  s.admin_id, s.s_category, c.c_name AS category_name
           FROM `System` AS s
           LEFT JOIN `Category` AS c ON c.c_id = s.s_category
           WHERE s.s_type = %s
             AND s.is_active = 1
           ORDER BY s.s_createdate DESC, s.s_id DESC
           """
           cur.execute(sql, (s_type,))
           rows = cur.fetchall()
       out = []
       for r in rows:
           d = r if isinstance(r, dict) else {
               "s_id": r[0], "s_text": r[1], "s_answer": r[2], "s_media_url": r[3],
               "s_createdate": r[4], "admin_id": r[5], "s_category": r[6], "category_name": r[7]
           }
           out.append(d)
       return jsonify(out)
   except Exception as e:
       return jsonify({"error": str(e)}), 500
   finally:
       conn.close()




# ========== 管理頁：更新 / 刪除 ==========
@bp.post("/api/system/update")
def api_system_update():
   """
   JSON: { s_id, s_text?, s_answer?, s_media_url? }
   """
   data = request.get_json(silent=True) or {}
   s_id = int(data.get("s_id") or 0)
   if not s_id:
       return jsonify({"error": "缺 s_id"}), 400


   fields, vals = [], []
   for key in ("s_text", "s_answer", "s_media_url"):
       if key in data:
           v = data[key]
           if isinstance(v, str):
               v = v.strip()
               if key == "s_media_url":
                   v = _promote_to_perm_if_needed(v)  # 若給 tmp，更新時也升級
           fields.append(f"{key}=%s")
           vals.append(v)


   if not fields:
       return jsonify({"error": "沒有可更新欄位"}), 400


   vals.append(s_id)


   conn = get_conn()
   try:
       with conn.cursor() as cur:
           sql = f"UPDATE `System` SET {', '.join(fields)}, s_createdate=NOW() WHERE s_id=%s"
           cur.execute(sql, tuple(vals))
       conn.commit()
       return jsonify({"ok": True})
   except Exception as e:
       conn.rollback()
       return jsonify({"error": str(e)}), 500
   finally:
       conn.close()


@bp.delete("/api/system/delete/<int:s_id>")
def api_system_delete(s_id: int):
   conn = get_conn()
   try:
       with conn.cursor() as cur:
           # 檢查是否被作答紀錄引用
           cur.execute("SELECT COUNT(*) FROM `SystemRecord` WHERE s_id=%s", (s_id,))
           ref_cnt = cur.fetchone()
           ref_cnt = (ref_cnt["COUNT(*)"] if isinstance(ref_cnt, dict) else ref_cnt[0]) or 0


           if ref_cnt > 0:
               # 有引用→改為下架，避免 1451
               cur.execute("UPDATE `System` SET is_active=0 WHERE s_id=%s", (s_id,))
               conn.commit()
               return jsonify({"ok": True, "soft": True, "msg": f"此題已有 {ref_cnt} 筆作答紀錄，已改為『下架』。"})


           # 無引用→允許物理刪除
           cur.execute("DELETE FROM `System` WHERE s_id=%s", (s_id,))
       conn.commit()
       return jsonify({"ok": True, "soft": False, "msg": "已刪除。"})
   except Exception as e:
       conn.rollback()
       return jsonify({"error": str(e)}), 500
   finally:
       conn.close()




# ========== 清理 tmp（手動 API；可排程）==========
@bp.get("/api/uploads/cleanup")
def api_cleanup_tmp():
   """
   /ai/api/uploads/cleanup?hours=6
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
# ========== 動圖裁切 API ==========
import uuid
import tempfile
import requests
import subprocess


def _download_or_open_local(url: str) -> str:
   """
   - 若是 /ai/uploads/... 就轉成本機路徑直接回傳
   - 其他 http(s) 再用 requests 抓到 temp 檔
   回傳本機檔案路徑
   """
   try:
       rel = _rel_from_public_url(url)  # 會把 /ai/uploads/ 去掉
       local_path = UPLOADS / rel
       if local_path.exists():
           return str(local_path)
   except Exception:
       pass


   # 外部或非本機的 URL → 下載
   r = requests.get(url, stream=True, timeout=30)
   r.raise_for_status()
   fd, path = tempfile.mkstemp(suffix=os.path.splitext(url)[1] or ".bin")
   with os.fdopen(fd, "wb") as f:
       shutil.copyfileobj(r.raw, f)
   return path  # 這是 temp 檔，要記得最後刪


def _run_ffmpeg(cmd: list[str]) -> None:
   p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
   if p.returncode != 0:
       raise RuntimeError(p.stderr.strip())
@bp.post("/api/crop-animated")
def crop_animated():
   data = request.get_json(force=True) or {}
   media_url = data.get("media_url") or ""
   try:
       x = int(data.get("x", 0))
       y = int(data.get("y", 0))
       w = int(data.get("w", 0))
       h = int(data.get("h", 0))
       rotate = int(data.get("rotate", 0) or 0)  # 0/90/180/270
       out_w = int(data.get("out_w", w or 0) or 0)
       out_h = int(data.get("out_h", h or 0) or 0)
   except Exception:
       return jsonify(ok=False, error="bad params"), 400


   if not (media_url and w > 0 and h > 0 and out_w > 0 and out_h > 0):
       return jsonify(ok=False, error="bad params"), 400


   # 來源檔（可能是本機、也可能是外部）
   try:
       src_path = _download_or_open_local(media_url)
   except Exception as e:
       return jsonify(ok=False, error=f"download source failed: {e}"), 400


   src_is_temp = not src_path.startswith(str(UPLOADS))  # 外部下載才需要刪除
   ext = (os.path.splitext(src_path)[1] or "").lower()
   is_gif = (ext == ".gif")
   out_ext = ".gif" if is_gif else ".mp4"


   # 輸出檔 → 直接進 perm/
   out_name = f"crop_{uuid.uuid4().hex}{out_ext}"
   out_path = PERM_DIR / out_name
   out_path.parent.mkdir(parents=True, exist_ok=True)


   # 濾鏡：先旋轉再裁切再縮放
   vf_parts = []
   if rotate % 360 != 0:
       rot = rotate % 360
       if rot == 90:   vf_parts.append("transpose=1")
       elif rot == 180:vf_parts.append("transpose=1,transpose=1")
       elif rot == 270:vf_parts.append("transpose=2")
   vf_parts.append(f"crop={w}:{h}:{x}:{y}")
   vf_parts.append(f"scale={out_w}:{out_h}:flags=lanczos")
   vf = ",".join(vf_parts)


   try:
       if is_gif:
           # GIF：兩段式 palette，適度降低 fps（加速 + 檔案較小）
           palette = str(out_path) + ".png"
           # 你可視情況把 fps= 設成 12~15
           _run_ffmpeg(["ffmpeg","-y","-i",src_path,"-vf",f"{vf},fps=15,palettegen",palette])
           _run_ffmpeg([
               "ffmpeg","-y","-i",src_path,"-i",palette,
               "-lavfi",f"{vf},fps=15[x];[x][1:v]paletteuse",
               "-loop","0",
               str(out_path)
           ])
           try: os.remove(palette)
           except Exception: pass
       else:
           # 視訊：H.264（為速度可用 ultrafast；品質優先則 veryfast/fast）
           _run_ffmpeg([
               "ffmpeg","-y","-i",src_path,
               "-vf", vf,
               "-map","0:v:0","-map","0:a?","-c:a","copy",
               "-c:v","libx264","-crf","20","-preset","veryfast",
               "-movflags","+faststart",
               str(out_path)
           ])
   except Exception as e:
       if src_is_temp:
           try: os.remove(src_path)
           except Exception: pass
       return jsonify(ok=False, error=str(e)), 500
   finally:
       if src_is_temp:
           try: os.remove(src_path)
           except Exception: pass


   public_url = _public_url_from_rel(f"perm/{out_name}")
   return jsonify(ok=True, media_url=public_url)



