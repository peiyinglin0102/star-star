# services/ai/ai_app.py
from pathlib import Path
import sys, os
from flask import Flask
from flask_cors import CORS
from flask import redirect

HERE = Path(__file__).resolve().parent   # services/ai
SERV = HERE.parent                       # services/
ROOT = SERV.parent                       # star-star/

# 讓 Python 找到專案內模組
for p in (str(SERV), str(ROOT)):
    if p not in sys.path:
        sys.path.append(p)

from ai import bp as ai_bp  # services/ai/__init__.py

app = Flask(__name__,
            template_folder=str(SERV / "templates"),
            static_folder=str(SERV / "static"))
CORS(app)

# 只掛「AI」這個 Blueprint，不干擾別人
app.register_blueprint(ai_bp, url_prefix="/ai")

@app.get("/ping")
def ping():
    return "pong"

@app.get("/")
def root_redirect():
    return redirect("/ai/")

if __name__ == "__main__":
    # 在容器內跑本機埠，不對外。每位同學可改用不同埠（如 5051/5052/5053）。
    port = int(os.getenv("AI_DEV_PORT", "5052"))
    print("TPL =", SERV / "templates")
    print("STA =", SERV / "static")
    app.run(host="127.0.0.1", port=port, debug=False)
