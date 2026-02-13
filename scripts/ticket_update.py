

#!/usr/bin/env python3
"""Notify ticket creators when their ticket is updated.

Design goals:
- Only notify the user who created the ticket (NOT the PI).
- Send an email every time something changes on a ticket, including what changed.
- Be schema-tolerant: tickets may use different field names for creator/updated timestamps.
- Be idempotent-ish: keep a per-ticket snapshot in `state/ticket_snapshots.json` to compute diffs.

Required env vars:
- FIREBASE_PROJECT_ID
- GCP_SA_KEY_B64  (base64-encoded service account JSON)
- SENDGRID_API_KEY
- MAIL_FROM

Optional env vars:
- SITE_URL        (used to build a link in the email)
- TICKETS_COLLECTION (default: 'tickets')
- STATE_DIR       (default: 'state')
- DEBUG           ('1' enables verbose logs)
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests
from google.cloud import firestore
from google.oauth2 import service_account


SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"


@dataclasses.dataclass
class Config:
    project_id: str
    sa_key_b64: str
    sendgrid_api_key: str
    mail_from: str
    site_url: str
    tickets_collection: str
    state_dir: str
    debug: bool


def _env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or v.strip() == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return v or ""


def load_config() -> Config:
    return Config(
        project_id=_env("FIREBASE_PROJECT_ID", required=True),
        sa_key_b64=_env("GCP_SA_KEY_B64", required=True),
        sendgrid_api_key=_env("SENDGRID_API_KEY", required=True),
        mail_from=_env("MAIL_FROM", required=True),
        site_url=_env("SITE_URL", default="").rstrip("/") + ("/" if _env("SITE_URL", default="").strip() else ""),
        tickets_collection=_env("TICKETS_COLLECTION", default="tickets"),
        state_dir=_env("STATE_DIR", default="state"),
        debug=_env("DEBUG", default="0") == "1",
    )


def debug(cfg: Config, msg: str) -> None:
    if cfg.debug:
        print(f"DEBUG: {msg}")


def ensure_state_dir(cfg: Config) -> None:
    os.makedirs(cfg.state_dir, exist_ok=True)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _to_iso(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc).isoformat()


def parse_firestore_timestamp(value: Any) -> Optional[dt.datetime]:
    """Best-effort parsing of Firestore timestamps.

    Supports:
    - google.cloud.firestore_v1._helpers.TimestampWithNanoseconds
    - datetime
    - ISO strings
    - dicts like {'seconds': ..., 'nanoseconds': ...}
    """
    if value is None:
        return None

    # Already a datetime
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value

    # Firestore TimestampWithNanoseconds has to_datetime()
    if hasattr(value, "to_datetime"):
        try:
            v = value.to_datetime()
            if isinstance(v, dt.datetime):
                if v.tzinfo is None:
                    return v.replace(tzinfo=dt.timezone.utc)
                return v
        except Exception:
            pass

    # Dict seconds/nanos
    if isinstance(value, dict) and "seconds" in value:
        try:
            seconds = int(value.get("seconds"))
            nanos = int(value.get("nanoseconds", 0))
            return dt.datetime.fromtimestamp(seconds + nanos / 1e9, tz=dt.timezone.utc)
        except Exception:
            return None

    # ISO-ish string
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Accept trailing Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            v = dt.datetime.fromisoformat(s)
            if v.tzinfo is None:
                v = v.replace(tzinfo=dt.timezone.utc)
            return v
        except Exception:
            return None

    return None


def get_ticket_creator_email(doc: Dict[str, Any]) -> Optional[str]:
    """Try multiple field names for the creator email."""
    candidates = [
        doc.get("creatorEmail"),
        doc.get("createdByEmail"),
        doc.get("requesterEmail"),
        doc.get("email"),
    ]

    # Some schemas nest creator info
    for key in ("createdBy", "creator", "requester", "user"):
        v = doc.get(key)
        if isinstance(v, dict):
            candidates.extend([v.get("email"), v.get("mail"), v.get("userEmail")])

    # First valid email
    for c in candidates:
        if isinstance(c, str) and "@" in c:
            return c.strip()

    return None


def get_ticket_updated_at(doc: Dict[str, Any]) -> Optional[dt.datetime]:
    """Try multiple field names for the updated timestamp."""
    for k in ("updatedAt", "lastUpdatedAt", "modifiedAt", "lastModifiedAt"):
        ts = parse_firestore_timestamp(doc.get(k))
        if ts is not None:
            return ts

    # Sometimes you only have createdAt but updates are kept in a subfield
    for k in ("createdAt",):
        ts = parse_firestore_timestamp(doc.get(k))
        if ts is not None:
            return ts

    return None


def canonicalize_for_snapshot(obj: Any) -> Any:
    """Make values JSON-stable and comparable."""
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dt.datetime):
        return _to_iso(obj)

    if hasattr(obj, "to_datetime"):
        try:
            return _to_iso(parse_firestore_timestamp(obj) or _now_utc())
        except Exception:
            return str(obj)

    if isinstance(obj, dict):
        return {str(k): canonicalize_for_snapshot(v) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))}

    if isinstance(obj, list):
        return [canonicalize_for_snapshot(v) for v in obj]

    # Fallback
    return str(obj)


def compute_diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Return a human-readable list of changes.

    We do a field-level diff on a curated set of keys, plus a fallback on top-level keys.
    """
    # Prefer a curated list if present in data
    preferred_keys = [
        "title",
        "status",
        "priority",
        "assignee",
        "assigneeEmail",
        "lab",
        "labKey",
        "category",
        "tags",
        "dueDate",
        "notes",
        "description",
        "lastComment",
        "commentCount",
        "updatedAt",
    ]

    keys: List[str] = []
    for k in preferred_keys:
        if k in old or k in new:
            keys.append(k)

    # If curated list is empty, compare a limited set of top-level keys (avoid exploding on huge blobs)
    if not keys:
        keys = sorted(set(list(old.keys()) + list(new.keys())))
        # Avoid noisy/system keys
        keys = [k for k in keys if k not in {"attachments", "history", "events"}]
        keys = keys[:25]

    changes: List[str] = []
    for k in keys:
        o = old.get(k)
        n = new.get(k)
        if o == n:
            continue

        # Nicify long strings
        def short(v: Any) -> str:
            if v is None:
                return "(empty)"
            if isinstance(v, str):
                s = re.sub(r"\s+", " ", v).strip()
                if len(s) > 160:
                    return s[:157] + "…"
                return s
            return str(v)

        changes.append(f"- {k}: {short(o)}  →  {short(n)}")

    return changes


def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        # Corrupted state should not kill notifications; start fresh.
        return default


def write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


def init_firestore(cfg: Config) -> firestore.Client:
    sa_json = base64.b64decode(cfg.sa_key_b64).decode("utf-8")
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=cfg.project_id, credentials=creds)


def send_email(cfg: Config, to_email: str, subject: str, text_body: str) -> None:
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": cfg.mail_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": text_body}],
    }

    r = requests.post(
        SENDGRID_API,
        headers={
            "Authorization": f"Bearer {cfg.sendgrid_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if r.status_code >= 300:
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")


def build_ticket_link(cfg: Config, ticket_id: str) -> str:
    # We don't know your router; keep it safe and useful.
    # If SITE_URL is set, at least point to the site + include the ticket id.
    if cfg.site_url:
        return f"{cfg.site_url}#ticket={ticket_id}"
    return ticket_id


def main() -> int:
    cfg = load_config()
    ensure_state_dir(cfg)

    state_last_run_path = os.path.join(cfg.state_dir, "last_run_updates.json")
    snapshots_path = os.path.join(cfg.state_dir, "ticket_snapshots.json")

    state = read_json(state_last_run_path, {"last_run": "2020-01-01T00:00:00+00:00"})
    last_run = parse_firestore_timestamp(state.get("last_run")) or dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)

    # Small lookback to avoid missing updates due to clock skew.
    since = last_run - dt.timedelta(minutes=3)

    snapshots: Dict[str, Any] = read_json(snapshots_path, {})

    db = init_firestore(cfg)

    # Query: updatedAt >= since (fallback later if updatedAt doesn't exist)
    # Note: if some docs don't have updatedAt, they won't appear here; for those you should ensure updatedAt is always set.
    query = (
        db.collection(cfg.tickets_collection)
        .where("updatedAt", ">=", since)
        .order_by("updatedAt")
    )

    docs = list(query.stream())
    debug(cfg, f"last_run={_to_iso(last_run)} since={_to_iso(since)} docs={len(docs)}")

    sent = 0
    scanned = 0

    for snap in docs:
        scanned += 1
        ticket_id = snap.id
        doc = snap.to_dict() or {}

        updated_at = get_ticket_updated_at(doc)
        if updated_at is None:
            debug(cfg, f"SKIP: ticket={ticket_id} missing/invalid updatedAt")
            continue

        creator_email = get_ticket_creator_email(doc)
        if not creator_email:
            debug(cfg, f"SKIP: ticket={ticket_id} missing creator email")
            continue

        # Build comparable snapshot
        new_snapshot = canonicalize_for_snapshot(doc)
        old_snapshot = snapshots.get(ticket_id, {}) if isinstance(snapshots.get(ticket_id), dict) else {}

        changes = compute_diff(old_snapshot, new_snapshot)

        # If this is the first time we see the ticket, don't spam with the whole doc.
        # Just store the snapshot and skip.
        if not old_snapshot:
            snapshots[ticket_id] = new_snapshot
            debug(cfg, f"INIT: ticket={ticket_id} stored first snapshot (no email)")
            continue

        if not changes:
            debug(cfg, f"NOCHANGE: ticket={ticket_id}")
            snapshots[ticket_id] = new_snapshot
            continue

        title = str(doc.get("title") or doc.get("name") or f"Ticket {ticket_id}")
        link = build_ticket_link(cfg, ticket_id)

        subject = f"Ticket update: {title}"
        body_lines = [
            f"Your ticket has been updated.",
            "",
            f"Ticket: {title}",
            f"ID: {ticket_id}",
            f"Updated at (UTC): {_to_iso(updated_at)}",
        ]
        if cfg.site_url:
            body_lines.append(f"Link: {link}")
        body_lines.extend([
            "",
            "Changes:",
            *changes,
            "",
            "—",
            "This is an automated notification.",
        ])

        try:
            send_email(cfg, creator_email, subject, "\n".join(body_lines))
            sent += 1
            print(f"SENT: ticket={ticket_id} to={creator_email} updatedAt={_to_iso(updated_at)}")
        except Exception as e:
            # Don't block the whole run; just log.
            print(f"ERROR: ticket={ticket_id} to={creator_email} err={e}")

        snapshots[ticket_id] = new_snapshot

    # Only move last_run forward after scanning finishes.
    new_last_run = _now_utc()
    write_json(state_last_run_path, {"last_run": _to_iso(new_last_run)})
    write_json(snapshots_path, snapshots)

    print(f"OK: scanned={scanned} sent={sent} since={_to_iso(since)} last_run={_to_iso(new_last_run)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"FATAL: {e}")
        raise