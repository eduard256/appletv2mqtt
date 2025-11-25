# Apple TV to MQTT Bridge

Простой и надежный MQTT-мост для управления Apple TV.

## Возможности

- 🎮 Управление Apple TV через MQTT команды
- 📊 Получение состояния воспроизведения в реальном времени
- 📱 Список установленных приложений
- 🔄 Автоматическое переподключение при потере связи
- 📝 Подробное логирование

## Требования

- Python 3.9+
- Доступ к Apple TV в локальной сети
- MQTT брокер (Mosquitto, EMQX, и т.д.)
- Выполненный pairing с Apple TV через `atvremote`

## Установка

### 1. Клонирование и настройка окружения

```bash
cd /home/dev/appletv2mqtt
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Получение credentials Apple TV

Сначала найдите ваш Apple TV:

```bash
atvremote scan
```

Запомните `Identifier` вашего устройства.

Затем выполните pairing:

```bash
atvremote pair
```

Следуйте инструкциям и введите PIN-код с экрана Apple TV. Сохраните полученные credentials.

### 3. Конфигурация

```bash
cp .env.example .env
nano .env
```

Заполните все параметры в `.env` файле.

### 4. Тестовый запуск

```bash
python main.py
```

## Настройка как systemd сервис

### 1. Создание сервиса

```bash
sudo nano /etc/systemd/system/appletv2mqtt.service
```

```ini
[Unit]
Description=Apple TV to MQTT Bridge
After=network.target

[Service]
Type=simple
User=dev
WorkingDirectory=/home/dev/appletv2mqtt
ExecStart=/home/dev/appletv2mqtt/venv/bin/python /home/dev/appletv2mqtt/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 2. Активация и запуск

```bash
sudo systemctl daemon-reload
sudo systemctl enable appletv2mqtt
sudo systemctl start appletv2mqtt
```

### 3. Просмотр логов

```bash
sudo journalctl -u appletv2mqtt -f
```

## MQTT Топики

### Публикуемые топики

| Топик | Описание | Retain |
|-------|----------|--------|
| `{base}/availability` | Статус подключения (`online`/`offline`) | Да |
| `{base}/state` | Текущее состояние воспроизведения (JSON) | Нет |
| `{base}/apps` | Список установленных приложений (JSON) | Да |

### Топики для команд

| Топик | Описание |
|-------|----------|
| `{base}/set` | Отправка команд управления |
| `{base}/get` | Принудительное обновление данных |

## Команды управления

Отправляйте JSON в топик `{base}/set`:

### Навигация

```json
{"action": "up"}
{"action": "down"}
{"action": "left"}
{"action": "right"}
{"action": "select"}
{"action": "menu"}
{"action": "home"}
```

### Медиа

```json
{"action": "play"}
{"action": "pause"}
{"action": "play_pause"}
{"action": "stop"}
{"action": "next"}
{"action": "previous"}
```

### Питание

```json
{"action": "turn_on"}
{"action": "turn_off"}
```

### Запуск приложения

```json
{"action": "launch_app", "app_id": "com.netflix.Netflix"}
```

### Воспроизведение URL

```json
{"action": "play_url", "url": "https://example.com/video.mp4"}
```

### Множественные команды

```json
{"action": "multi", "commands": ["up", "up", "select"]}
```

## Принудительное обновление

Отправьте JSON в топик `{base}/get`:

```json
{"type": "state"}   // Обновить состояние
{"type": "apps"}    // Обновить список приложений
{"type": "all"}     // Обновить всё
```

## Формат данных состояния

```json
{
  "media_type": "Music",
  "device_state": "Playing",
  "title": "Song Name",
  "artist": "Artist Name",
  "album": "Album Name",
  "genre": "Genre",
  "position": 65,
  "total_time": 240,
  "repeat": "Off",
  "shuffle": "Off",
  "app": "Music",
  "app_id": "com.apple.TVMusic",
  "power_state": "On"
}
```

## Формат списка приложений

```json
[
  {"name": "Netflix", "id": "com.netflix.Netflix"},
  {"name": "YouTube", "id": "com.google.ios.youtube"},
  {"name": "Apple TV", "id": "com.apple.TVWatchList"}
]
```

## Интеграция с Home Assistant

Пример конфигурации для Home Assistant:

```yaml
mqtt:
  sensor:
    - name: "Apple TV State"
      state_topic: "appletv/state"
      value_template: "{{ value_json.device_state }}"
      json_attributes_topic: "appletv/state"

    - name: "Apple TV App"
      state_topic: "appletv/state"
      value_template: "{{ value_json.app }}"

  binary_sensor:
    - name: "Apple TV Available"
      state_topic: "appletv/availability"
      payload_on: "online"
      payload_off: "offline"
```

## Устранение неполадок

### Apple TV не найден

1. Проверьте, что Apple TV и сервер в одной сети
2. Убедитесь, что avahi-daemon запущен: `systemctl status avahi-daemon`
3. Проверьте правильность `APPLETV_ID` через `atvremote scan`

### Ошибка аутентификации

1. Повторите pairing: `atvremote pair`
2. Обновите `APPLETV_CREDENTIALS` в `.env`

### MQTT не подключается

1. Проверьте доступность брокера: `mosquitto_pub -h HOST -t test -m "test"`
2. Проверьте учетные данные в `.env`

## Лицензия

MIT
