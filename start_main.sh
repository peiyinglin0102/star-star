#!/bin/bash

# 1. 強制進入目錄
cd /workspaces/main

echo "=== [PROD] 正式站重啟 (Port: 5009) ==="

# 2. 停掉舊的 (改用 Port 號來殺，更精準，不會誤殺自己)
sudo fuser -k 5009/tcp || true
sudo pkill -9 -f "gunicorn.*5009" || true
sleep 1

# 3. 確保 Log 權限
sudo mkdir -p /var/log/gunicorn
sudo chmod 777 /var/log/gunicorn

# 4. 關鍵：進入虛擬環境，並直接用 gunicorn 指令
source /workspaces/main/.venv/bin/activate

# 5. 執行
gunicorn -w 4 -k gthread \
  -b 127.0.0.1:5009 \
  --timeout 300 \
  --chdir /workspaces/main \
  --pid /tmp/guni-prod.pid \
  --access-logfile /var/log/gunicorn/prod.access.log \
  --error-logfile /var/log/gunicorn/prod.error.log \
  services.app:app -D

# 6. 驗證
echo "等待啟動檢查..."
sleep 3
ss -ltnp | grep 5009
