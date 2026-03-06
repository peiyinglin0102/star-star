# /workspaces/main/services/ai/utils_text.py
import random
import re

# —— 六類英文標籤（相容既有）——
EMOTION_EN = ["angry", "disgust", "fear", "happy", "sad", "surprise"]

# —— 英中對照（相容既有）——
EMOTION_ZH = {
    "angry": "生氣",
    "disgust": "厭惡",
    "fear": "害怕",
    "happy": "開心",
    "sad": "難過",
    "surprise": "驚訝",
}

EMOTION_ZH_2_EN = {v: k for k, v in EMOTION_ZH.items()}
EMOTION_ZH_LIST = ["開心", "難過", "生氣", "害怕", "驚訝", "厭惡"]

# —— 童言短句模板（無空格，不超過15字）——
# 一律保證有主詞；若前端沒傳 role，改用 CORE_WHOS 代詞。
CORE_WHOS = [
    "這個人","畫面裡的人","照片裡的人","圖中的人","這位",
]

# 問「心情/情緒/表情」的核心片段（短、童言）
WHAT_BITS = [
    "心情是什麼","感覺是什麼","是什麼表情","像什麼情緒","現在什麼表情",
    "現在的心情","此刻的心情","像什麼心情","是什麼情緒","像哪種心情",
    "看起來像什麼","看起來是什麼","臉上是什麼","會是哪個心情","最可能是什麼",
    "心裡是什麼","情緒像什麼","哪個心情","是哪個表情","像哪個表情",
]

# 動圖提示短語
GIF_HINTS = [
    "動畫裡的{who}","動圖裡的{who}","連續畫面裡的{who}","影片中的{who}",
    "動起來的{who}","小影片裡的{who}","動態畫面裡的{who}",
]

# 圖片提示短語
PIC_HINTS = [
    "圖片裡的{who}","照片裡的{who}","畫面中的{who}","圖中的{who}",
    "相片裡的{who}","這張圖的{who}","鏡頭裡的{who}",
]

# 超短模板：全含 {who}，避免沒主詞
ULTRA_SHORT = [
    "{who}什麼表情？",
    "{who}什麼情緒？",
    "{who}現在感覺？",
    "{who}是什麼情緒？",
    "{who}心情是什麼？",
    "{who}是哪個心情？",
    "{who}看起來如何？",
    "{who}感覺像哪個？",
    "{who}表情是哪個？",
    "{who}現在像什麼？",
]

# 童言語助詞（最後仍統一成問號）
TAIL_BITS = ["呢"]

# 正常短句模板（修正為完整自然句）
TEMPLATES = [
    "{who}{what}？",
    "{who}現在感覺呢？",
    "{who}看起來像哪個？",
    "{who}此刻心情呢？",
    "{who}現在像什麼？",
    "{who}給你什麼感覺？",
    "{who}讓你想到哪個？",
    "你覺得{who}是什麼？",
    "{who}可能是什麼？",
    "{who}心情是哪個？",
    "{who}臉上像什麼？",
    "{who}表情像哪個？",
    "{who}現在是什麼？",
    "{who}會是哪個呢？",
    "{who}像哪個心情？",
    "{who}在想什麼呢？",
    "{who}感覺是哪個？",
    "{who}像哪種情緒？",
]

# 動圖專屬模板（修正不完整句）
GIF_TEMPLATES = [
    "{hint}{what}？",
    "{hint}感覺呢？",
    "{hint}像什麼？",
    "{hint}是什麼？",
    "{hint}心情是哪個？",
    "{hint}表情像哪個？",
    "{hint}現在像什麼？",
    "{hint}你覺得呢？",
    "{hint}會是哪個？",
    "{hint}看起來是什麼？",
]

# 圖片專屬模板（修正不完整句）
PIC_TEMPLATES = [
    "{hint}{what}？",
    "{hint}感覺呢？",
    "{hint}像什麼？",
    "{hint}是什麼？",
    "{hint}心情是哪個？",
    "{hint}表情像哪個？",
    "{hint}現在像什麼？",
    "{hint}你覺得呢？",
    "{hint}會是哪個？",
    "{hint}看起來是什麼？",
]

# 可愛副詞
CUTE_FILLERS = [
    "好像","有點","看起來","偷偷","輕輕","小小","有沒有","是不是","可能",
    "比較像","最像","有點像","感覺像","看起來像",
]

# —— 小工具 ——


def _no_space(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _end_with_q(s: str) -> str:
    s = s.rstrip("。！!?.？")
    return s + "？"


def _limit_len(s: str, max_len: int = 15) -> str:
    s = _no_space(s)
    if len(s) <= max_len:
        return s
    s = s[: max_len - 1]
    return _end_with_q(s)


def _pick_who(role: str | None) -> str:
    """永遠保證有主詞；若 role 無或未使用則從 CORE_WHOS 隨機挑"""
    if role:
        return role
    return random.choice(CORE_WHOS)


def _should_inject_role(meta: dict | None) -> bool:
    """
    若 meta.inject_role=True → 一定帶入角色；
    否則以 0.7 機率帶入（當 role 存在時）。
    """
    if not meta:
        return False
    if meta.get("inject_role") is True:
        return True
    return random.random() < 0.7


def _safe_filler_for_key(key: str) -> str | None:
    """
    依關鍵詞挑選「不會造成重複或拗口」的副詞。
    - 若 key 含「看起來」：禁用「看起來」「看起來像」
    - 若 key 含「像」：禁用含「像」字的副詞（避免「像像」）
    - 若 key 含「感覺」：禁用「感覺像」（避免「感覺像像」）
    """
    ban = set()
    if "看起來" in key:
        ban.update({"看起來", "看起來像"})
    if "像" in key:
        for f in CUTE_FILLERS:
            if "像" in f:
                ban.add(f)
    if "感覺" in key:
        ban.add("感覺像")

    candidates = [f for f in CUTE_FILLERS if f not in ban]
    if not candidates:
        return None
    return random.choice(candidates)


def _maybe_cute_infix(s: str) -> str:
    """小機率在句中插入安全副詞（僅在可插位置）"""
    if random.random() >= 0.4:
        return s

    key = None
    for k in ["看起來是什麼", "看起來像什麼", "看起來", "像什麼", "感覺", "心情", "表情", "哪個", "是什麼", "什麼"]:
        if k in s:
            key = k
            break
    if not key:
        return s

    filler = _safe_filler_for_key(key)
    if not filler:
        return s

    # 僅替換第一次出現的位置，避免重複
    return s.replace(key, filler + key, 1)


def _maybe_tail(s: str) -> str:
    """
    句尾語氣字：避免「呢呢？」重複。
    僅在句尾不含「呢」時，才加一次。
    """
    stripped = re.sub(r"[。！!?.？]+$", "", s or "")
    if stripped.endswith("呢"):
        return s
    if random.random() < 0.35:
        s = stripped + "呢"
    return s


def gen_question_text(emotion_en: str, meta: dict = None) -> str:
    """
    產生童言、短句、無空格的題幹（≤15字，最多兩個標點，實際只用結尾問號）。
    meta 可帶：
      - s_type: "圖片" | "動圖" | 其他
      - role: "阿姨" / "小朋友" / ...
      - inject_role: True/False（True=一定帶入；False/未給=0.7 機率帶入）
    """
    meta = meta or {}
    s_type = meta.get("s_type", "圖片")
    role = (meta.get("role") or "").strip()

    # 是否嘗試帶入角色
    use_role = bool(role) and _should_inject_role(meta)
    who_core = role if use_role else _pick_who(None)

    # 構句片段
    what = random.choice(WHAT_BITS)

    # 題型提示 + 模板
    if s_type == "動圖":
        hint = random.choice(GIF_HINTS).format(who=who_core)
        base = random.choice(GIF_TEMPLATES).format(hint=hint, what=what)
    elif s_type == "圖片":
        if random.random() < 0.5:
            hint = random.choice(PIC_HINTS).format(who=who_core)
            base = random.choice(PIC_TEMPLATES).format(hint=hint, what=what)
        else:
            base = random.choice(TEMPLATES).format(who=who_core, what=what)
            base = _maybe_cute_infix(base)
    else:
        base = random.choice(TEMPLATES).format(who=who_core, what=what)
        base = _maybe_cute_infix(base)

    # 偶爾換成超短句（仍保證有主詞）
    if random.random() < 0.25:
        base = random.choice(ULTRA_SHORT).format(who=who_core)

    # 童言尾詞（避免重複）
    base = _maybe_tail(base)

    # 清理、收尾與長度限制
    base = _no_space(base)
    base = _end_with_q(base)
    base = _limit_len(base, 15)

    # 再次確保標點不超過兩個
    puncts = re.findall(r"[，,。.!！？?、]", base)
    if len(puncts) > 2:
        base = re.sub(r"[，,。.!！？?、]", "", base)
        base = _end_with_q(base)

    return base
