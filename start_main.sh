#!/bin/bash

# 1. 確保腳本在專案根目錄執行
cd "$(dirname "$0")"

echo "=== [DEV] Star-Star 測試站重啟中 ==="

# 2. 強制關閉佔用 5001 埠號的舊行程
echo "[1/4] 清理 Port 5001..."
sudo fuser -k 5001/tcp || true
sudo pkill -f "gunicorn.*starstar" || true

# 3. 確保 Log 資料夾存在 (避免 Gunicorn 因為找不到路徑而啟動失敗)
echo "[2/4] 檢查日誌目錄..."
mkdir -p /tmp/starstar
chmod 777 /tmp/starstar

# 4. 使用本地虛擬環境啟動 Gunicorn
echo "[3/4] 啟動 Gunicorn (Port: 5001)..."
./.venv/bin/python3 -m gunicorn -w 4 -k gthread \
  -b 127.0.0.1:5001 \
  --timeout 300 \
  --daemon \
  --pid /tmp/guni-dev-18360.pid \
  --access-logfile /tmp/starstar/dev-18360.access.log \
  --error-logfile /tmp/starstar/dev-18360.error.log \
  services.app:app

# 5. 驗證結果
sleep 2  # 等待 2 秒讓 Gunicorn 跑起來
if ps aux | grep -v grep | grep "5001" > /dev/null
then
    echo "[4/4] ✅ 重啟成功！測試站已在 http://127.0.0.1:5001 運行"
    echo "      (外網請訪問: http://140.128.10.26)"
else
    echo "[4/4] ❌ 啟動失敗！請檢查日誌: /tmp/starstar/dev-18360.error.log"
    exit 1
fi