# 🌟 StarStar - 自閉症兒童的情緒辨識訓練系統

使用者登入:https://starlearning.duckdns.org:18361/
後台管理:https://starlearning.duckdns.org:18361/admin/login

「**自閉症兒童的情緒辨識訓練系統 (Emotion Recognition Training System)**」是一個專為自閉症孩童設計的網頁應用程式。本系統結合豐富的互動式前端介面與後端人工智慧 (AI) 模型，透過情緒教學、互動遊戲、以及即時臉部情緒辨識測驗，協助孩童認識、理解並學習表達各種情緒。

---

## 🚀 核心功能與特色

系統將核心功能劃分多個子模組（Blueprint），提供完整的學習與管理體驗：

- **📚 教學模組 (`teach`, `learn`)**：提供漸進式的情緒認知教學內容（如：`Teach.html`、`Learningprocess.html`），以圖文及互動方式引導幼童學習基本情緒。`learn` 藍圖位於 `services/quiz/learn.py`。
- **🎮 遊戲與測驗模組 (`game`, `quiz`)**：
  - 內建互動式的情緒辨識遊戲模組（包含記憶翻牌、射擊、堆疊遊戲等），增加學習趣味性 (`Play.html`)。
  - 多樣化的測驗評估機制 (`Quiz.html`, `Quiz_list.html`)，並整合 **OpenAI API (GPT-4o-mini)** 針對孩童答題狀況產出溫暖、動態的專屬回饋語，同步追蹤長期與短期的學習雷達圖進度。
- **🤖 AI 臉部辨識擴充 (`ai`, `ai/custom`)**：
<<<<<<< HEAD
  - **核心辨識**: 整合 FER (表情辨識) 與 **MediaPipe** (高效人臉偵測)，針對 6 大核心情緒（生氣、厭惡、害怕、開心、難過、驚訝）提供分析。
  - **多幀動態分析**: 支援靜態圖與 **GIF/WebP 動圖**，系統會自動等距抽樣多個候選幀，並透過信心值與情緒修正規則（如判斷 fear/sad 權重調整）篩選最佳結果。
  - **自動化題幹生成**: 內建童言短句生成器 (`utils_text.py`)，能根據情緒類別與媒體類型自動產出適合幼童的互動問題。
  - **自訂擴充**: 支援使用者自訂題庫，並整合 FFmpeg 處理影像裁切與轉檔 (`Add_Question.html`, `Customize.html`)。
- **🔐 身份驗證與安全 (`auth`, `admin`)**：
  - **auth**: 提供安全的註冊、登入機制。採用 **Werkzeug Security** 動態密碼雜湊與信箱 **OTP 註冊驗證**。
  - **admin**: 系統管理員專屬的後台路徑 `/admin`，其實作位於 `services/auth/admin_routes.py`。

---

## 🛠 技術架構 (Tech Stack)

本專案採用典型的 Python 全端架構，並將環境依賴區分為「Web 應用與 API」與「AI 視覺推論」兩大部分。

### Backend (後端程式)
- **核心框架**: [Flask](https://flask.palletsprojects.com/) 3.0
- **資料庫與 ORM**: MySQL、[SQLAlchemy](https://www.sqlalchemy.org/) 2.0、Alembic
- **伺服器**: Gunicorn (WSGI 佈署)
- **安全性**: Werkzeug Security, Flask-Limiter, Flask-Mail (信箱 OTP 驗證)。

### AI & Machine Learning (人工智慧與機器學習)
- **語言模型 (LLM)**: OpenAI API (`gpt-4o-mini`) 動態生成幼兒鼓勵回饋語。
- **特徵偵測**: **MediaPipe** (Face Detection), **FER** (Facial Expression Recognition)。
- **深度學習框架**: **TensorFlow 2.10.1 (Keras)**, Numpy (1.23.5), Pandas (1.5.3)。
- **影像與電腦視覺**: OpenCV (Headless) 影像處理, **Pillow** (PIL 圖像增強與動圖抽幀), **imageio**。
- **多媒體處理**: **FFmpeg** (透過 `subprocess` 或 `moviepy` 進行裁切與轉檔)。

### Frontend (前端介面)
- **模板引擎**: Jinja2 (Flask 內建)
- **前端技術**: HTML5, CSS3, JavaScript (原生 / 動態更新)

---

## 📂 專案目錄結構

```text
/workspaces/main/
├── services/                 # 後端應用程式碼根目錄
│   ├── app.py                # 系統進入點 (主程式)
│   ├── auth/                 # 身份驗證與後台管理模組
│   │   ├── routes.py         # 註冊、登入與 OTP
│   │   └── admin_routes.py   # 後台管理路由 (/admin)
│   ├── quiz/                 # 評量與學習歷程模組
│   ├── game/                 # 遊戲邏輯模組
│   ├── teach/                # 教學內容模組
│   ├── ai/                   # AI 情緒辨識核心模組
│   │   ├── predict_emotion.py # 核心辨識算法 (Multi-frame Logic)
│   │   ├── utils_text.py     # 自動化童言題幹生成
│   │   ├── routes.py         # 系統預設 AI 路由 (/ai)
│   │   └── custom/           # 使用者自訂題庫模組 (/ai/custom)
│   ├── static/               # 前端靜態資源
│   └── templates/            # 前端網頁模板
├── requirements.txt          # Web 核心依賴
├── requirements-ai.txt       # AI 辨識依賴 (TensorFlow, FER, etc.)
└── start_main.sh             # 系統啟動腳本
```

---

## ⚙️ 安裝與環境建置

您可以依照下述步驟，於 Linux 或 MacOS 環境中快速建置並啟動此專案：

### 1. 系統需求 (Prerequisites)
- **Python**: 建議使用 Python 3.9 或以上版本。
- **Database**: 建議安裝並運行 MySQL 伺服器 (若為本機開發，也可相容 SQLite)。

### 2. 建立虛擬環境與安裝依賴包
系統中的依賴被分為 Web 端與 AI 端獨立管理：

```bash
# 建立並啟動 Python 虛擬環境
python3 -m venv .venv
source .venv/bin/activate

# 1. 安裝系統與後端網站所需套件
pip install -r requirements.txt

# 2. 安裝 AI 臉部辨識與推論所需套件
pip install -r requirements-ai.txt
```

### 3. 環境變數設定 (.env)
將 `.env.example` 複製一份並重新命名為 `.env`，並確保在 `.env` 中設定好資料庫與應用的所需變數：

```bash
cp .env.example .env
```
請開啟 `.env` 設定資料庫連線、`SECRET_KEY` 等重要私密資訊。

```ini
# .env 內容範例：
DB_HOST=127.0.0.1       # 或您的資料庫位址
DB_PORT=3306            # MySQL 預設 3306
DB_USER=your_user       # 您的資料庫使用者
DB_PASSWORD=your_pass   # 您的資料庫密碼
DB_NAME=your_db_name    # 您的資料庫名稱
# ...等其它設定
```

---

## 🚀 運行專案 (Running the Application)

### 開發模式 (Development)
若您要在本機上進行除錯與開發，可直接透過 Python 啟動：

```bash
python services/app.py
```
> 開發伺服器預設會啟動在 `http://0.0.0.0:18361/`。

### 產品佈署模式 (Production deployment)
專案內提供了快速啟動服務的腳本檔案 (`start_main.sh`)，該腳本會透過 **Gunicorn** 將服務常駐並運行。

```bash
# 賦予腳本執行權限（第一次運行時）
chmod +x start_main.sh

# 執行啟動腳本
./start_main.sh
```
啟動成功後，服務預設會以 `4 workers` 及 `gthread` 模式並綁定於 `127.0.0.1:5009`，您可以透過 Nginx 或 Apache 進行 Reverse Proxy (反向代理)，並綁定 SSL 憑證 (HTTPS) 提供對外服務。

> **日誌路徑**：
> 伺服器的正常存取與錯誤 Log 記錄檔將會存放於 `/var/log/gunicorn/` 資料夾內。
