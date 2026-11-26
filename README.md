# blablabla

## Монитор талонов (Воложин ЦРБ)

Часовая проверка: `python3 scripts/check_volozhin_tickets.py --json`

Инструкции для автоматизации: [.cursor/automation-volozhin-tickets-prompt.md](.cursor/automation-volozhin-tickets-prompt.md)

Секреты: `VOLZOHIN_TICKETS_BOT_TOKEN`, опционально `VOLZOHIN_TICKETS_CHAT_ID`.

Другой врач: только `VOLZOHIN_TICKETS_ROOM_ID` (или `--room-id`). ФИО и учреждение скрипт читает со страницы.
