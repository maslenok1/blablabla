# Воложин — свободные талоны (каждый час)

**Не открывай браузер и не ставь Playwright** — страница отдаёт статический HTML.

## Запуск

```bash
python3 scripts/check_volozhin_tickets.py --json
```

Скрипт сам:

1. Загружает `https://medic-service.by/VolozhinCRB/ticketGet/?room_id={ROOM}` (один GET). По умолчанию `room_id=401`.
2. Читает со страницы **ФИО врача** (первый заголовок h3, без пометки в скобках) и **учреждение** (название в «ёлочках» в шапке).
3. Если ФИО не нашлось — ничего не отправляет (`skip_no_doctor`).
4. Ищет только элементы с классом **TimeItem** и атрибутами `ticketDate`/`TicketDate` и `ticketTime`/`TicketTime`.
5. Сравнивает пары дата+время с `sent_pairs` в state-файле.
6. При **новых** талонах шлёт **одно** сообщение в Telegram. На приём **не записывается**.

Если страница не открылась, новых талонов нет или отправка недоступна — сообщение не уходит (см. поле `action` в JSON).

## Смена врача

Меняется **только** `room_id`. ФИО и учреждение в тексте уведомления скрипт берёт со страницы.

```bash
export VOLZOHIN_TICKETS_ROOM_ID=123
python3 scripts/check_volozhin_tickets.py --json
```

Или разово: `python3 scripts/check_volozhin_tickets.py --room-id 123 --json`.

У каждого `room_id` свой файл истории (`volozhin-room-{room_id}-sent.json`). Для кабинета 401, если уже есть старый файл, используется `volozhin-prokopovich-401-sent.json`.

## Секреты (обязательно для отправки)

В секретах окружения агента должны быть:

- `VOLZOHIN_TICKETS_BOT_TOKEN` — токен бота (**не выводить в лог и ответ**).
- `VOLZOHIN_TICKETS_CHAT_ID` — если не задан или Telegram отвечает `chat not found`, скрипт шлёт в `-5127409441` (чат «Dentist appointment»). Короткий положительный id вроде номера кабинета чатом не является.

## Состояние между запусками

По умолчанию: `/cursor/stores/automation/memories/volozhin-prokopovich-401-sent.json`  
Запасной путь в репозитории: `scripts/.volozhin-tickets-state.json`  
Переопределение: `VOLZOHIN_TICKETS_STATE_FILE`.

## Отчёт в чат

Кратко: `action`, `slots_total`, `slots_new`, `doctor_name`, `org`. Токен и URL с токеном не упоминать.