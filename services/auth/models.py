# services/auth/models.py
from datetime import datetime
from . import db

# ---------------- 使用者 ----------------
class User(db.Model):
    __tablename__ = "User"   # 注意大小寫與資料庫一致

    user_id = db.Column(db.Integer, primary_key=True)              # 使用者編號
    name = db.Column(db.String(50), nullable=False)                # 姓名
    gender = db.Column(db.String(50), nullable=False)              # 性別
    birthday = db.Column(db.DateTime, nullable=False)              # 生日
    password_hash = db.Column(db.String(255), nullable=False)      # 密碼雜湊
    email = db.Column(db.String(100), unique=True, nullable=False) # 電子郵件
    is_verified = db.Column(db.Boolean, default=False)             # 是否通過驗證
    reset_token = db.Column(db.String(255), nullable=True)         # 重設驗證碼
    register_date = db.Column(db.DateTime, default=lambda: datetime.utcnow())  # 註冊日期
    last_login    = db.Column(db.DateTime, nullable=True)            # 上次登入時間
    status = db.Column(db.String(50), default="active")            # 帳號狀態（啟用/停用）
    avatar = db.Column(db.String(255), nullable=True)              # ★ 新增：頭像（如 avatars/3.png）

# ---------------- 暫存驗證 ----------------
# services/auth/models.py
class PendingVerify(db.Model):
    __tablename__ = "pending_verify"
    token         = db.Column(db.String(512), primary_key=True)
    email         = db.Column(db.String(256), nullable=False)
    name          = db.Column(db.String(120), nullable=False)
    gender        = db.Column(db.String(16))
    birthday      = db.Column(db.String(20))
    password_hash = db.Column(db.String(255), nullable=False)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.utcnow())
    otp           = db.Column(db.String(6), nullable=True)       # ← 必須有
    otp_expire_at = db.Column(db.DateTime, nullable=True)        # ← 僅此一個
    avatar        = db.Column(db.String(255), nullable=True)
# ---------------- 管理員 ----------------
class Admin(db.Model):
    __tablename__ = "Admin"  # ⚠️ 必須與資料庫實際表名完全一致（區分大小寫）

    # 欄位定義，與 DB 結構對應：
    admin_id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # 管理員編號 (int(11) AI PK)
    name = db.Column(db.String(50), nullable=True)                          # 管理員姓名 (varchar(50))
    username = db.Column(db.String(50), unique=True, nullable=False)        # 帳號 (varchar(50))
    password_hash = db.Column(db.String(255), nullable=False)               # 密碼雜湊 (varchar(255))
