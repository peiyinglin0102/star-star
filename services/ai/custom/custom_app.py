# services/ai/custom/custom_app.py
from pathlib import Path
import sys, os
from flask import Flask, redirect, jsonify
from flask_cors import CORS

# --- 路徑設定 ---
CUSTOM = Path(__file__).resolve().parent        # services/ai/custom
AI_DIR = CUSTOM.parent                          # services/ai
SERV = AI_DIR.parent                            # services/
ROOT = SERV.parent                              # 專案根目錄

# 讓 Python 找得到：services/ai、services、專案根目錄
for p in (str(AI_DIR), str(SERV), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 載入藍圖（注意：import custom，不要 import ai.custom）
from custom import bp as custom_bp

app = Flask(
    __name__,
    template_folder=str(SERV / "templates"),  # 你的前端模板在 services/templates/
    static_folder=str(SERV / "static"),       # 靜態檔在 services/static/
)
CORS(app)
app.register_blueprint(custom_bp, url_prefix="/ai/custom")

# 根路徑與健康檢查（避免打 127.0.0.1:5053/ 出現 404）
@app.get("/")
def root():
    return redirect("/ai/custom/")

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "custom_app", "prefix": "/ai/custom"}), 200

if __name__ == "__main__":
    port = int(os.getenv("AI_CUSTOM_DEV_PORT", "5053"))
    app.run(host="127.0.0.1", port=port, debug=True)