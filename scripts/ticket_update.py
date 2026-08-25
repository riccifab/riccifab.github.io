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
- BREVO_API_KEY
- MAIL_FROM

Optional env vars:
- SITE_URL        (used to build a link in the email)
- TICKETS_COLLECTION (default: 'tickets')
- STATE_DIR       (default: 'state')
- DEBUG           ('1' enables verbose logs)
- MAIL_CC        (comma-separated CC recipients; optional)
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

from google.cloud import firestore
from google.oauth2 import service_account
from brevo_mail import send_brevo_email


@dataclasses.dataclass
class Config:
    project_id: str
    sa_key_b64: str
    brevo_api_key: str
    mail_from: str
    mail_cc: str
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
        brevo_api_key=_env("BREVO_API_KEY", required=True),
        mail_from=_env("MAIL_FROM", required=True),
        mail_cc=_env("MAIL_CC", default=""),
        site_url=_env("SITE_URL", default="").rstrip("/") + ("" if _env("SITE_URL", default="").strip() else ""),
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
    for k in ("updatedAt", "lastUpdatedAt", "modifiedAt", "lastModifiedAt", "lastCommentAt"):
        ts = parse_firestore_timestamp(doc.get(k))
        if ts is not None:
            return ts

    # Sometimes you only have createdAt but updates are kept in a subfield
    for k in ("createdAt",):
        ts = parse_firestore_timestamp(doc.get(k))
        if ts is not None:
            return ts

    return None


def collect_recent_comment_ticket_ids(cfg: Config, db: firestore.Client, since: dt.datetime) -> set[str]:
    ticket_ids: set[str] = set()
    try:
        query = (
            db.collection_group("comments")
            .where("createdAt", ">=", since)
            .order_by("createdAt")
        )
        for snap in query.stream():
            parent = snap.reference.parent.parent
            if parent and parent.id:
                ticket_ids.add(parent.id)
    except Exception as exc:
        debug(cfg, f"comment-scan failed: {exc}")
    return ticket_ids


def load_comment_metadata(cfg: Config, db: firestore.Client, tickets_collection: str, ticket_id: str) -> Dict[str, Any]:
    comments = list(db.collection(tickets_collection).document(ticket_id).collection("comments").stream())
    if not comments:
        return {}

    latest_payload: Dict[str, Any] = {}
    latest_at: Optional[dt.datetime] = None
    for snap in comments:
        payload = snap.to_dict() or {}
        created_at = parse_firestore_timestamp(payload.get("createdAt"))
        if latest_at is None or (created_at is not None and created_at >= latest_at):
            latest_at = created_at
            latest_payload = payload

    return {
        "commentCount": len(comments),
        "lastComment": str(latest_payload.get("text") or latest_payload.get("comment") or "").strip(),
        "lastCommentAuthorEmail": str(latest_payload.get("authorEmail") or latest_payload.get("email") or "").strip(),
        "lastCommentAt": latest_at,
    }


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
        "deliveryDate",
        "deliveryAt",
        "expectedDelivery",
        "eta",
        "deadline",
        "notes",
        "description",
        "lastComment",
        "lastCommentAuthorEmail",
        "lastCommentAt",
        "commentCount",
    ]

    keys: List[str] = []
    for k in preferred_keys:
        if k in old or k in new:
            keys.append(k)

    # Also include any other top-level keys that actually changed, otherwise we may miss
    # updates stored under non-preferred keys (e.g. `delivery` dict).
    noisy = {"updatedAt", "createdAt", "attachments", "history", "events", "requesterSubmission", "requesterSubmittedAt"}
    all_keys = set(old.keys()) | set(new.keys())
    extra_changed = []
    for k in all_keys:
        if k in noisy or k in keys:
            continue
        if old.get(k) != new.get(k):
            extra_changed.append(k)
    extra_changed = sorted(extra_changed)[:15]
    keys.extend(extra_changed)

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


def parse_recipients(value: str) -> List[str]:
    """Parse a comma/semicolon/space separated list of emails."""
    if not value:
        return []
    parts = re.split(r"[;,\s]+", value.strip())
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if "@" not in p:
            continue
        out.append(p)
    # Deduplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for e in out:
        if e in seen:
            continue
        seen.add(e)
        uniq.append(e)
    return uniq


def send_email(cfg: Config, to_email: str, subject: str, text_body: str) -> None:
    cc_list = parse_recipients(cfg.mail_cc)
    cc_list = [e for e in cc_list if e.lower() != to_email.lower()]

    send_brevo_email(
        api_key=cfg.brevo_api_key,
        mail_from=cfg.mail_from,
        to_list=[to_email],
        subject=subject,
        body=text_body,
        cc_list=cc_list,
    )


def build_ticket_link(cfg: Config, ticket_id: str) -> str:
    # We don't know your router; keep it safe and useful.
    # If SITE_URL is set, at least point to the site + include the ticket id.
    if cfg.site_url:
        return f"{cfg.site_url}#ticket={ticket_id}"
    return ticket_id


def build_stats_link(site_url: str) -> str:
    if not site_url:
        return ""
    url = site_url.split("#", 1)[0].rstrip("/")
    if url.endswith("/labticketstats.html") or url.endswith("labticketstats.html"):
        return url
    if url.endswith("/tickets.html") or url.endswith("tickets.html"):
        return url[: -len("tickets.html")] + "labticketstats.html"
    if url.endswith(".html"):
        base = url.rsplit("/", 1)[0]
        return f"{base}/labticketstats.html"
    return f"{url}/labticketstats.html"


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
    comment_ticket_ids = collect_recent_comment_ticket_ids(cfg, db, since)
    debug(
        cfg,
        f"last_run={_to_iso(last_run)} since={_to_iso(since)} docs={len(docs)} comment_tickets={len(comment_ticket_ids)}",
    )

    candidate_ids: List[str] = []
    candidate_docs: Dict[str, Dict[str, Any]] = {}
    for snap in docs:
        candidate_ids.append(snap.id)
        candidate_docs[snap.id] = snap.to_dict() or {}

    for ticket_id in sorted(comment_ticket_ids):
        if ticket_id in candidate_docs:
            continue
        snap = db.collection(cfg.tickets_collection).document(ticket_id).get()
        if not snap.exists:
            debug(cfg, f"SKIP: comment-linked ticket={ticket_id} no longer exists")
            continue
        candidate_ids.append(ticket_id)
        candidate_docs[ticket_id] = snap.to_dict() or {}

    sent = 0
    scanned = 0
    failed = 0

    for ticket_id in candidate_ids:
        scanned += 1
        doc = dict(candidate_docs.get(ticket_id) or {})

        if ticket_id in comment_ticket_ids:
            doc.update(load_comment_metadata(cfg, db, cfg.tickets_collection, ticket_id))

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

        subject = f"Ticket update: {title}"
        body_lines = [
            f"Your ticket has been updated.",
            "",
            f"Ticket: {title}",
            f"ID: {ticket_id}",
            f"Updated at (UTC): {_to_iso(updated_at)}",
        ]
        if cfg.site_url:
            body_lines.append(f"Ticket portal: {build_ticket_link(cfg, ticket_id)}")
            stats_link = build_stats_link(cfg.site_url)
            if stats_link:
                body_lines.append(f"Stats page: {stats_link}")
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
            failed += 1
            print(f"ERROR: ticket={ticket_id} to={creator_email} err={e}")
            continue

        snapshots[ticket_id] = new_snapshot

    # Keep the previous cursor when a delivery fails so the update is retried.
    new_last_run = last_run if failed else _now_utc()
    write_json(state_last_run_path, {"last_run": _to_iso(new_last_run)})
    write_json(snapshots_path, snapshots)

    result = "ERROR" if failed else "OK"
    print(
        f"{result}: scanned={scanned} sent={sent} failed={failed} "
        f"since={_to_iso(since)} last_run={_to_iso(new_last_run)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"FATAL: {e}")
        raise
