# Воложин — свободные талоны (каждый час)

**Не открывай браузер и не ставь Playwright** — страница отдаёт статический HTML.

## Запуск

```bash
python3 scripts/check_volozhin_tickets.py --json
```

Скрипт сам:

1. Загружает `https://medic-service.by/VolozhinCRB/ticketGet/?room_id=401` (один GET).
2. Проверяет, что на странице есть **Прокопович Мария Вячеславовна**.
3. Ищет только элементы с классом **TimeItem** и атрибутами `ticketDate`/`TicketDate` и `ticketTime`/`TicketTime` (пустые ячейки календаря игнорируются).
4. Сравнивает пары дата+время с `sent_pairs` в state-файле.
5. При **новых** талонах шлёт **одно** сообщение в Telegram (Bot API). На приём **не записывается**.

Если врача нет, страница не открылась, новых талонов нет или отправка недоступна — сообщение не уходит (см. поле `action` в JSON).

## Смена врача / кабинета

Без правки кода — переменные окружения (или флаги CLI):

| Переменная | Назначение | По умолчанию |
|------------|------------|--------------|
| `VOLZOHIN_TICKETS_ROOM_ID` | `room_id` в URL medic-service | `401` |
| `VOLZOHIN_TICKETS_DOCTOR_MARKER` | Подстрока ФИО **на странице** (проверка, что открыли нужного врача) | `Прокопович` |
| `VOLZOHIN_TICKETS_DOCTOR_NAME` | Полное ФИО **в тексте Telegram** | `Прокопович Мария Вячеславовна` |
| `VOLZOHIN_TICKETS_ORG` | Учреждение в сообщении | `Воложинская ЦРБ` |
| `VOLZOHIN_TICKETS_PAGE_URL` | Целиком другой URL (если не Воложин / не room_id) | из `room_id` |

Пример другого врача (подставь `room_id` со страницы записи на medic-service):

```bash
export VOLZOHIN_TICKETS_ROOM_ID=123
export VOLZOHIN_TICKETS_DOCTOR_MARKER="Иванов"
export VOLZOHIN_TICKETS_DOCTOR_NAME="Иванов Иван Иванович"
python3 scripts/check_volozhin_tickets.py --json
```

Или разово: `--room-id 123 --doctor-marker Иванов --doctor-name "Иванов Иван Иванович"`.

**Важно:** у каждого `room_id` свой файл истории отправок (`volozhin-room-{room_id}-sent.json`), чтобы талоны разных врачей не смешивались.

## Секреты (обязательно для отправки)

В секретах окружения агента должны быть:

- `VOLZOHIN_TICKETS_BOT_TOKEN` — токен бота (**не выводить в лог и ответ**).
- `VOLZOHIN_TICKETS_CHAT_ID` — по умолчанию `-5127409441`, если не задан.

## Состояние между запусками

По умолчанию: `/cursor/stores/automation/memories/volozhin-prokopovich-401-sent.json`  
Запасной путь в репозитории: `scripts/.volozhin-tickets-state.json`  
Переопределение: `VOLZOHIN_TICKETS_STATE_FILE`.

## Отчёт в чат

Кратко: `action`, `slots_total`, `slots_new`, `doctor_present`. Токен и URL с токеном не упоминать.