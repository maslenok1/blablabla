#!/usr/bin/env python3
"""
Проверка свободных талонов medic-service.by (статическая вёрстка, один HTTP GET).
Состояние «уже отправлено» — JSON с sent_pairs. Токен бота только из окружения.

Врача и кабинет можно менять без правки кода — см. VOLZOHIN_TICKETS_* в .cursor/automation-volozhin-tickets-prompt.md
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

MEDIC_PAGE_TEMPLATE = (
    "https://medic-service.by/VolozhinCRB/ticketGet/?room_id={room_id}"
)
DEFAULT_ROOM_ID = "401"
DEFAULT_DOCTOR_MARKER = "Прокопович"
DEFAULT_DOCTOR_NAME = "Прокопович Мария Вячеславовна"
DEFAULT_ORG_LABEL = "Воложинская ЦРБ"
USER_AGENT = (
    "Mozilla/5.0 (compatible; VolozhinTicketsMonitor/1.0; +https://medic-service.by)"
)
DEFAULT_CHAT_ID = "-5127409441"

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = Path("/cursor/stores/automation/memories")
LEGACY_STATE = MEMORY_DIR / "volozhin-prokopovich-401-sent.json"

TIME_ITEM_TAG = re.compile(r"<[^>]+\bTimeItem\b[^>]*>", re.IGNORECASE)
ATTR_DATE = re.compile(r'(?:ticketDate|TicketDate)\s*=\s*["\']([^"\']+)', re.I)
ATTR_TIME = re.compile(r'(?:ticketTime|TicketTime)\s*=\s*["\']([^"\']+)', re.I)


@dataclass(frozen=True)
class MonitorConfig:
    room_id: str
    page_url: str
    doctor_marker: str
    doctor_name: str
    org_label: str

    def state_path(self, override: str | None) -> Path:
        if override:
            return Path(override)
        room_state = MEMORY_DIR / f"volozhin-room-{self.room_id}-sent.json"
        if self.room_id == DEFAULT_ROOM_ID and LEGACY_STATE.is_file():
            return LEGACY_STATE
        if MEMORY_DIR.is_dir():
            return room_state
        return REPO_ROOT / "scripts" / f".volozhin-tickets-{self.room_id}-state.json"


def load_config(
    *,
    room_id: str | None = None,
    doctor_marker: str | None = None,
    doctor_name: str | None = None,
    org_label: str | None = None,
    page_url: str | None = None,
) -> MonitorConfig:
    rid = (room_id or os.environ.get("VOLZOHIN_TICKETS_ROOM_ID") or DEFAULT_ROOM_ID).strip()
    marker = (
        doctor_marker
        or os.environ.get("VOLZOHIN_TICKETS_DOCTOR_MARKER")
        or DEFAULT_DOCTOR_MARKER
    ).strip()
    name = (
        doctor_name
        or os.environ.get("VOLZOHIN_TICKETS_DOCTOR_NAME")
        or DEFAULT_DOCTOR_NAME
    ).strip()
    org = (
        org_label or os.environ.get("VOLZOHIN_TICKETS_ORG") or DEFAULT_ORG_LABEL
    ).strip()
    url = (
        page_url
        or os.environ.get("VOLZOHIN_TICKETS_PAGE_URL")
        or MEDIC_PAGE_TEMPLATE.format(room_id=rid)
    ).strip()
    return MonitorConfig(
        room_id=rid,
        page_url=url,
        doctor_marker=marker,
        doctor_name=name,
        org_label=org,
    )


@dataclass
class Slot:
    date: str  # YYYY-MM-DD
    time: str  # HH:MM

    @property
    def key(self) -> str:
        return f"{self.date}|{self.time}"


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"sent_pairs": []}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if "sent_pairs" not in data:
        data["sent_pairs"] = []
    return data


def save_state(path: Path, data: dict, cfg: MonitorConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["doctor"] = cfg.doctor_name
    data["room_id"] = cfg.room_id
    data["last_checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_html(url: str, timeout: float = 25.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
    seen: set[str] = set()
    unique: list[Slot] = []
    for s in slots:
        if s.key not in seen:
            seen.add(s.key)
            unique.append(s)
    return unique


def format_message(new_slots: list[Slot], cfg: MonitorConfig) -> str:
    by_date: dict[str, list[str]] = {}
    for s in new_slots:
        by_date.setdefault(s.date, []).append(s.time)
    lines = [
        "Появились свободные талоны.",
        cfg.doctor_name,
        cfg.org_label,
        "",
    ]
    for date in sorted(by_date.keys()):
        d = datetime.strptime(date, "%Y-%m-%d")
        times = ", ".join(sorted(by_date[date]))
        lines.append(f"{d.strftime('%d.%m.%Y')}: {times}")
    lines.append("")
    lines.append(f"Запись: {cfg.page_url}")
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
        api,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Монитор талонов medic-service.by (Воложин ЦРБ)"
    )
    parser.add_argument("--json", action="store_true", help="Итог JSON в stdout")
    parser.add_argument("--dry-run", action="store_true", help="Без отправки в Telegram")
    parser.add_argument(
        "--room-id",
        help="room_id в URL (или VOLZOHIN_TICKETS_ROOM_ID)",
    )
    parser.add_argument(
        "--doctor-marker",
        help="Подстрока ФИО на странице (или VOLZOHIN_TICKETS_DOCTOR_MARKER)",
    )
    parser.add_argument(
        "--doctor-name",
        help="Полное ФИО в сообщении (или VOLZOHIN_TICKETS_DOCTOR_NAME)",
    )
    parser.add_argument(
        "--org",
        help="Название учреждения в сообщении (или VOLZOHIN_TICKETS_ORG)",
    )
    args = parser.parse_args()

    cfg = load_config(
        room_id=args.room_id,
        doctor_marker=args.doctor_marker,
        doctor_name=args.doctor_name,
        org_label=args.org,
    )
    state_path = cfg.state_path(os.environ.get("VOLZOHIN_TICKETS_STATE_FILE"))

    result = {
        "ok": False,
        "action": "none",
        "doctor_present": False,
        "room_id": cfg.room_id,
        "doctor_name": cfg.doctor_name,
        "slots_total": 0,
        "slots_new": 0,
        "state_file": str(state_path),
        "error": None,
    }

    try:
        html = fetch_html(cfg.page_url)
        result["doctor_present"] = cfg.doctor_marker in html
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
            save_state(state_path, state, cfg)
            result["ok"] = True
            result["action"] = "no_new_slots"
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            return 0

        message = format_message(new_slots, cfg)
        send_telegram(message, dry_run=args.dry_run)

        if not args.dry_run:
            for s in new_slots:
                sent.add(s.key)
            state["sent_pairs"] = sorted(sent)
            state["last_note"] = f"Отправлено новых слотов: {len(new_slots)}"
            save_state(state_path, state, cfg)
            result["action"] = "sent"
        else:
            result["action"] = "would_send"

        result["ok"] = True
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 0

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