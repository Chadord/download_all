# YouTube Downloader

Веб-застосунок для завантаження відео через браузер. Запускається на Raspberry Pi (або будь-якому ПК), доступний з будь-якого пристрою в локальній мережі.

## Можливості

- Якість: 4K / 1080p / 720p / 480p / 360p / MP3
- Кілька відео паралельно з прогрес-барами
- Завантаження всіх відео одним ZIP-архівом
- Автовидалення файлів через 2 години
- Адаптивний темний інтерфейс (iOS/Android/ПК)

## Встановлення

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

## Запуск

```bash
python downloader_app.py
```

Відкрити у браузері: `http://<IP-адреса>:5050`

## Деплой на Raspberry Pi

```bash
sudo bash setup_pi.sh
```

Після цього застосунок доступний як `http://ytdl.local:5050` і автоматично стартує після перезавантаження.

## Вимоги

- Python 3.10+
- ffmpeg (`sudo apt install ffmpeg`)
