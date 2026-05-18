#!/bin/bash
# Запускати один раз на чистому Raspberry Pi OS
# sudo bash setup_pi.sh

set -e

APP_DIR="/home/pi/ytdl"
SERVICE="downloader"

echo "=== 1. Оновлення системи ==="
apt-get update -y
apt-get install -y python3 python3-pip python3-venv ffmpeg git avahi-daemon

echo "=== 2. Клонування репозиторію ==="
if [ ! -d "$APP_DIR" ]; then
  git clone https://github.com/YOUR_USER/YOUR_REPO.git "$APP_DIR"
else
  echo "Директорія вже є, оновлюємо..."
  git -C "$APP_DIR" pull
fi

echo "=== 3. Віртуальне середовище ==="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== 4. Папки для завантажень ==="
mkdir -p "$APP_DIR/downloads/.serve"
chown -R pi:pi "$APP_DIR"

echo "=== 5. mDNS ім'я (ytdl.local) ==="
HOSTNAME_FILE="/etc/hostname"
echo "ytdl" > "$HOSTNAME_FILE"
hostnamectl set-hostname ytdl
systemctl enable avahi-daemon
systemctl restart avahi-daemon

echo "=== 6. systemd сервіс ==="
cp "$APP_DIR/downloader.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo ""
echo "✅ Готово!"
echo "   Відкривай у браузері: http://ytdl.local:5050"
echo "   Або за IP: http://$(hostname -I | awk '{print $1}'):5050"
echo ""
echo "Корисні команди:"
echo "  sudo systemctl status $SERVICE    — статус"
echo "  sudo journalctl -u $SERVICE -f    — логи в реальному часі"
echo "  sudo systemctl restart $SERVICE   — перезапуск"
