#!/usr/bin/env python3
"""
Проверка свободных талонов medic-service.by (статическая вёрстка, один HTTP GET).
Состояние «уже отправлено» — JSON с sent_pairs. Токен бота только из окружения.

Меняется только room_id. ФИО врача и название учреждения читаются со страницы.
"""
from __future__ import annotations

import argparse
import html as html_lib
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
H3_BLOCK = re.compile(r"<h3[^>]*>(.*?)</h3>", re.I | re.S)
H2_QUOTED = re.compile(r"<h2[^>]*>\s*«([^»]+)»", re.I)
TITLE_QUOTED = re.compile(r"<title>[^<]*«([^»]+)»", re.I)


@dataclass(frozen=True)
class MonitorConfig:
    room_id: str
    page_url: str

    def state_path(self, override: str | None) -> Path:
        if override:
            return Path(override)
        room_state = MEMORY_DIR / f"volozhin-room-{self.room_id}-sent.json"
        if self.room_id == DEFAULT_ROOM_ID and LEGACY_STATE.is_file():
            return LEGACY_STATE
        if MEMORY_DIR.is_dir():
            return room_state
        return REPO_ROOT / "scripts" / f".volozhin-tickets-{self.room_id}-state.json"


def load_config(*, room_id: str | None = None) -> MonitorConfig:
    rid = (room_id or os.environ.get("VOLZOHIN_TICKETS_ROOM_ID") or DEFAULT_ROOM_ID).strip()
    if not rid.isdigit():
        raise ValueError(f"room_id должен быть числом, получено: {rid!r}")
    return MonitorConfig(
        room_id=rid,
        page_url=MEDIC_PAGE_TEMPLATE.format(room_id=rid),
    )


def _plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_doctor_name(page_html: str) -> str | None:
    """Первый заголовок h3 с ФИО, без пометки в скобках вроде «внебюджетная деятельность»."""
    for match in H3_BLOCK.finditer(page_html):
        text = _plain(match.group(1))
        if not text or text.lower().startswith("расписание"):
            continue
        text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
        words = text.split()
        if len(words) >= 2 and re.search(r"[А-Яа-яЁё]", text):
            return text
    return None


def extract_org_label(page_html: str) -> str:
    """Название учреждения из шапки («Воложинская ЦРБ»), без префикса «УЗ»."""
    for raw in reversed(H2_QUOTED.findall(page_html)):
        name = re.sub(r"^УЗ\s+", "", html_lib.unescape(raw).strip())
        if name and "medic" not in name.lower():
            return name
    title = TITLE_QUOTED.search(page_html)
    if title:
        return re.sub(r"^УЗ\s+", "", html_lib.unescape(title.group(1)).strip())
    return ""


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


def save_state(
    path: Path,
    data: dict,
    cfg: MonitorConfig,
    *,
    doctor_name: str,
    org_label: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["doctor"] = doctor_name
    data["org"] = org_label
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


def format_message(
    new_slots: list[Slot],
    cfg: MonitorConfig,
    *,
    doctor_name: str,
    org_label: str,
) -> str:
    by_date: dict[str, list[str]] = {}
    for s in new_slots:
        by_date.setdefault(s.date, []).append(s.time)
    lines = [
        "Появились свободные талоны.",
        doctor_name,
    ]
    if org_label:
        lines.append(org_label)
    lines.append("")
    for date in sorted(by_date.keys()):
        d = datetime.strptime(date, "%Y-%m-%d")
        times = ", ".join(sorted(by_date[date]))
        lines.append(f"{d.strftime('%d.%m.%Y')}: {times}")
    lines.append("")
    lines.append(f"Запись: {cfg.page_url}")
    return "\n".join(lines)


class TelegramSendError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        description: str = "",
        error_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.description = description
        self.error_code = error_code


def redact_secrets(text: str) -> str:
    redacted = text
    for key in ("VOLZOHIN_TICKETS_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
        secret = os.environ.get(key) or ""
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def chat_id_candidates() -> list[str]:
    """Сначала заданный chat id, затем чат «Dentist appointment», если это не он."""
    raw = (os.environ.get("VOLZOHIN_TICKETS_CHAT_ID") or "").strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
    if DEFAULT_CHAT_ID not in candidates:
        candidates.append(DEFAULT_CHAT_ID)
    return candidates


def _telegram_description(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return ""
    return str(payload.get("description") or "")


def post_telegram(token: str, chat_id: str, text: str) -> None:
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        desc = _telegram_description(e.read())
        raise TelegramSendError(
            redact_secrets(f"telegram_http_{e.code}: {desc or e.reason}"),
            description=desc,
            error_code=e.code,
        ) from None
    if not payload.get("ok"):
        desc = str(payload.get("description") or "")
        raise TelegramSendError(
            redact_secrets(f"telegram_api: {desc}"),
            description=desc,
        )


def send_telegram(text: str, *, dry_run: bool) -> str:
    token = os.environ.get("VOLZOHIN_TICKETS_BOT_TOKEN") or os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )
    candidates = chat_id_candidates()
    if not token:
        raise TelegramSendError(
            "Не задан VOLZOHIN_TICKETS_BOT_TOKEN (или TELEGRAM_BOT_TOKEN)"
        )
    if dry_run:
        return candidates[0]
    last: TelegramSendError | None = None
    for index, chat_id in enumerate(candidates):
        try:
            post_telegram(token, chat_id, text)
            return chat_id
        except TelegramSendError as exc:
            last = exc
            missing = "chat not found" in exc.description.lower()
            if missing and index < len(candidates) - 1:
                continue
            raise
    assert last is not None
    raise last


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Монитор талонов medic-service.by (Воложин ЦРБ)"
    )
    parser.add_argument("--json", action="store_true", help="Итог JSON в stdout")
    parser.add_argument("--dry-run", action="store_true", help="Без отправки в Telegram")
    parser.add_argument(
        "--room-id",
        help="room_id в URL (или VOLZOHIN_TICKETS_ROOM_ID). ФИО и учреждение берутся со страницы.",
    )
    args = parser.parse_args()

    try:
        cfg = load_config(room_id=args.room_id)
    except ValueError as e:
        print(json.dumps({"ok": False, "action": "error", "error": str(e)}, ensure_ascii=False))
        return 2
    state_path = cfg.state_path(os.environ.get("VOLZOHIN_TICKETS_STATE_FILE"))

    result = {
        "ok": False,
        "action": "none",
        "doctor_present": False,
        "room_id": cfg.room_id,
        "doctor_name": None,
        "org": None,
        "slots_total": 0,
        "slots_new": 0,
        "chat_id": None,
        "state_file": str(state_path),
        "error": None,
    }

    try:
        html = fetch_html(cfg.page_url)
        doctor_name = extract_doctor_name(html)
        org_label = extract_org_label(html)
        result["doctor_name"] = doctor_name
        result["org"] = org_label or None
        result["doctor_present"] = bool(doctor_name)
        if not doctor_name:
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
            save_state(
                state_path,
                state,
                cfg,
                doctor_name=doctor_name,
                org_label=org_label,
            )
            result["ok"] = True
            result["action"] = "no_new_slots"
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            return 0

        message = format_message(
            new_slots, cfg, doctor_name=doctor_name, org_label=org_label
        )
        result["chat_id"] = send_telegram(message, dry_run=args.dry_run)

        if not args.dry_run:
            for s in new_slots:
                sent.add(s.key)
            state["sent_pairs"] = sorted(sent)
            state["last_note"] = f"Отправлено новых слотов: {len(new_slots)}"
            save_state(
                state_path,
                state,
                cfg,
                doctor_name=doctor_name,
                org_label=org_label,
            )
            result["action"] = "sent"
        else:
            result["action"] = "would_send"

        result["ok"] = True
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 0

    except TelegramSendError as e:
        result["error"] = redact_secrets(str(e))
        result["action"] = "telegram_error"
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 2
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        result["error"] = redact_secrets(f"fetch_failed: {e}")
        result["action"] = "skip_fetch_error"
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 2
    except Exception as e:  # noqa: BLE001 — CLI boundary
        result["error"] = redact_secrets(str(e))
        result["action"] = "error"
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())