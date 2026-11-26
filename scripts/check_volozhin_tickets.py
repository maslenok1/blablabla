#!/usr/bin/env python3
"""
Проверка свободных талонов medic-service.by (статическая вёрстка, один HTTP GET).
Состояние «уже отправлено» — JSON с sent_pairs. Токен бота только из окружения.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

URL = "https://medic-service.by/VolozhinCRB/ticketGet/?room_id=401"
DOCTOR_MARKER = "Прокопович"
DOCTOR_FULL = "Прокопович Мария Вячеславовна"
ORG_LABEL = "Воложинская ЦРБ"
RECORD_URL = URL
USER_AGENT = (
    "Mozilla/5.0 (compatible; VolozhinTicketsMonitor/1.0; +https://medic-service.by)"
)
DEFAULT_CHAT_ID = "-5127409441"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATHS = (
    Path("/cursor/stores/automation/memories/volozhin-prokopovich-401-sent.json"),
    REPO_ROOT / "scripts" / ".volozhin-tickets-state.json",
)

TIME_ITEM_TAG = re.compile(r"<[^>]+\bTimeItem\b[^>]*>", re.IGNORECASE)
ATTR_DATE = re.compile(r'(?:ticketDate|TicketDate)\s*=\s*["\']([^"\']+)', re.I)
ATTR_TIME = re.compile(r'(?:ticketTime|TicketTime)\s*=\s*["\']([^"\']+)', re.I)


@dataclass
class Slot:
    date: str  # YYYY-MM-DD
    time: str  # HH:MM

    @property
    def key(self) -> str:
        return f"{self.date}|{self.time}"


def resolve_state_path() -> Path:
    override = os.environ.get("VOLZOHIN_TICKETS_STATE_FILE")
    if override:
        return Path(override)
    for p in DEFAULT_STATE_PATHS:
        if p.parent.exists() or p == DEFAULT_STATE_PATHS[-1]:
            return p
    return DEFAULT_STATE_PATHS[-1]


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"sent_pairs": []}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if "sent_pairs" not in data:
        data["sent_pairs"] = []
    return data


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["doctor"] = DOCTOR_FULL
    data["room_id"] = "401"
    data["last_checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_html(timeout: float = 25.0) -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def parse_slots(html: str) -> list[Slot]:
    slots: list[Slot] = []
    for tag in TIME_ITEM_TAG.findall(html):
        dm = ATTR_DATE.search(tag)
        tm = ATTR_TIME.search(tag)
        if dm and tm:
            slots.append(Slot(dm.group(1).strip(), tm.group(1).strip()))
    # дедуп с сохранением порядка
    seen: set[str] = set()
    unique: list[Slot] = []
    for s in slots:
        if s.key not in seen:
            seen.add(s.key)
            unique.append(s)
    return unique


def format_message(new_slots: list[Slot]) -> str:
    by_date: dict[str, list[str]] = {}
    for s in new_slots:
        by_date.setdefault(s.date, []).append(s.time)
    lines = [
        "Появились свободные талоны.",
        DOCTOR_FULL,
        ORG_LABEL,
        "",
    ]
    for date in sorted(by_date.keys()):
        d = datetime.strptime(date, "%Y-%m-%d")
        times = ", ".join(sorted(by_date[date]))
        lines.append(f"{d.strftime('%d.%m.%Y')}: {times}")
    lines.append("")
    lines.append(f"Запись: {RECORD_URL}")
    return "\n".join(lines)


def send_telegram(text: str, *, dry_run: bool) -> None:
    token = os.environ.get("VOLZOHIN_TICKETS_BOT_TOKEN") or os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )
    chat_id = os.environ.get("VOLZOHIN_TICKETS_CHAT_ID", DEFAULT_CHAT_ID)
    if not token:
        raise RuntimeError(
            "Не задан VOLZOHIN_TICKETS_BOT_TOKEN (или TELEGRAM_BOT_TOKEN)"
        )
    if dry_run:
        return
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text}
    ).encode("utf-8")
    req = urllib.request.Request(
        api, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Монитор талонов Воложин ЦРБ, кабинет 401")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Итог одной строкой JSON в stdout (без секретов)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не отправлять в Telegram, даже если есть новые талоны",
    )
    args = parser.parse_args()

    state_path = resolve_state_path()
    result = {
        "ok": False,
        "action": "none",
        "doctor_present": False,
        "slots_total": 0,
        "slots_new": 0,
        "state_file": str(state_path),
        "error": None,
    }

    try:
        html = fetch_html()
        result["doctor_present"] = DOCTOR_MARKER in html
        if not result["doctor_present"]:
            result["ok"] = True
            result["action"] = "skip_no_doctor"
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            return 0

        slots = parse_slots(html)
        result["slots_total"] = len(slots)

        state = load_state(state_path)
        sent: set[str] = set(state.get("sent_pairs", []))
        new_slots = [s for s in slots if s.key not in sent]
        result["slots_new"] = len(new_slots)

        if not new_slots:
            state["last_note"] = (
                f"Слотов TimeItem: {len(slots)}, новых для уведомления: 0"
            )
            save_state(state_path, state)
            result["ok"] = True
            result["action"] = "no_new_slots"
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            return 0

        message = format_message(new_slots)
        send_telegram(message, dry_run=args.dry_run)

        if not args.dry_run:
            for s in new_slots:
                sent.add(s.key)
            state["sent_pairs"] = sorted(sent)
            state["last_note"] = f"Отправлено новых слотов: {len(new_slots)}"
            save_state(state_path, state)
            result["action"] = "sent"
        else:
            result["action"] = "would_send"

        result["ok"] = True
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if args.dry_run or result["action"] == "sent" else 0

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        result["error"] = f"fetch_failed: {e}"
        result["action"] = "skip_fetch_error"
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 2
    except Exception as e:  # noqa: BLE001 — CLI boundary
        result["error"] = str(e)
        result["action"] = "error"
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())