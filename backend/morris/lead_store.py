from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


LEAD_FIELDS = ["name", "contact", "space_type", "preferred_time"]


@dataclass
class LeadDraft:
    active: bool = False
    name: str = ""
    contact: str = ""
    space_type: str = ""
    preferred_time: str = ""
    saved: bool = False
    last_prompt: str = ""
    history: list[str] = field(default_factory=list)

    def next_missing(self) -> str | None:
        for field_name in LEAD_FIELDS:
            if not getattr(self, field_name):
                return field_name
        return None


class LeadStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    email TEXT,
                    phone TEXT,
                    space_type TEXT,
                    preferred_date TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, draft: LeadDraft) -> int:
        email, phone = split_contact(draft.contact)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO leads (name, email, phone, space_type, preferred_date, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.name,
                    email,
                    phone,
                    draft.space_type,
                    draft.preferred_time,
                    "\n".join(draft.history),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 10) -> list[dict[str, str]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def split_contact(contact: str) -> tuple[str, str]:
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", contact)
    phone_match = re.search(r"(?:\+?\d[\d\s().-]{6,}\d)", contact)
    email = email_match.group(0) if email_match else ""
    phone = phone_match.group(0) if phone_match else ("" if email else contact)
    return email, phone


def update_lead_from_text(draft: LeadDraft, text: str) -> str:
    clean = text.strip()
    draft.history.append(f"Visitor: {clean}")

    if not draft.active:
        draft.active = True
        draft.last_prompt = "Yes, I can help with that. May I take your name first?"
        return draft.last_prompt

    missing = draft.next_missing()
    if missing == "name":
        draft.name = clean
        draft.last_prompt = "Thanks. What is the best email address or phone number for you?"
    elif missing == "contact":
        draft.contact = clean
        draft.last_prompt = "And what kind of space are you looking for?"
    elif missing == "space_type":
        draft.space_type = clean
        draft.last_prompt = "What day or time would suit you for a viewing?"
    elif missing == "preferred_time":
        draft.preferred_time = clean
        draft.last_prompt = "Thanks, I have saved that for the Morris Park team."
    else:
        draft.last_prompt = "Thanks, I have saved that for the Morris Park team."

    draft.history.append(f"AI: {draft.last_prompt}")
    return draft.last_prompt
