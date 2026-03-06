#!/bin/bash
# === 啟動 main 服務（對應 HTTPS :443 / 5009 Port）===
cd /workspaces/main

# 確保虛擬環境啟動
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "錯誤: 找不到 .venv 目錄！"
    exit 1
fi

# 確保 log 存放目錄存在 (需要 sudo 權限)
sudo mkdir -p /var/log/gunicorn/
sudo chown -R $USER:$USER /var/log/gunicorn/

echo "正在關閉舊的 Gunicorn 行程..."
sudo pkill -f "gunicorn.*main" || true

echo "正在以 Production 模式啟動 Gunicorn..."
gunicorn -w 4 -k gthread \
  -b 127.0.0.1:5009 \
  --timeout 300 \
  --chdir /workspaces/main \
  --pid /tmp/guni-prod.pid \
  --access-logfile /var/log/gunicorn/prod.access.log \
  --error-logfile  /var/log/gunicorn/prod.error.log \
  services.app:app -D

echo "Gunicorn 啟動腳本執行完畢！以下是 5009 Port 的監聽狀態："
ss -ltnp | grep 5009

