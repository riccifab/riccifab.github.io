#!/usr/bin/env python3
"""Sync IN_PROGRESS tickets from Firestore into work_status Realtime DB data."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from typing import Any

import requests
from google.auth.transport.requests import Request
from google.cloud import firestore
from google.oauth2 import service_account
from lab_names import canonical_lab_name, clean_lab_name, normalize_lab_key


DEFAULT_RTDB_URL = "https://workstatus-5a293-default-rtdb.europe-west1.firebasedatabase.app"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/datastore",
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
DEFAULT_GROUP = os.getenv("WORKSTATUS_FALLBACK_GROUP") or "General"


PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]
RTDB_URL = (os.getenv("FIREBASE_RTDB_URL") or DEFAULT_RTDB_URL).rstrip("/")
TICKETS_COLLECTION = os.getenv("TICKETS_COLLECTION") or "tickets"
KNOWN_LABS = [
    lab.strip()
    for lab in (os.getenv("WORKSTATUS_PI_LABS") or "Gozzi,Iurilli,Lombardo,Rossi").split(",")
    if lab.strip()
]
LAB_ALIASES = {normalize_lab_key(lab): lab for lab in KNOWN_LABS}


def load_credentials() -> service_account.Credentials:
    info = json.loads(base64.b64decode(os.environ["GCP_SA_KEY_B64"]).decode("utf-8"))
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def db_client(creds: service_account.Credentials) -> firestore.Client:
    return firestore.Client(project=PROJECT_ID, credentials=creds)


def parse_timestamp(value: Any) -> dt.datetime | None:
    if value is None:
        return None

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value

    if hasattr(value, "to_datetime"):
        try:
            converted = value.to_datetime()
        except Exception:
            converted = None
        if isinstance(converted, dt.datetime):
            if converted.tzinfo is None:
                return converted.replace(tzinfo=dt.timezone.utc)
            return converted

    if isinstance(value, dict) and "seconds" in value:
        try:
            seconds = int(value.get("seconds"))
            nanos = int(value.get("nanoseconds", 0))
            return dt.datetime.fromtimestamp(seconds + nanos / 1e9, tz=dt.timezone.utc)
        except Exception:
            return None

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed

    return None


def ticket_sort_key(ticket: dict[str, Any]) -> tuple[int, str, float, str]:
    priority = PRIORITY_ORDER.get((ticket.get("priority") or "").strip(), 99)
    expected = (ticket.get("expectedDeliveryDate") or "9999-12-31").strip()
    updated_at = parse_timestamp(ticket.get("updatedAt")) or parse_timestamp(ticket.get("createdAt"))
    updated_key = -(updated_at.timestamp()) if updated_at else 0.0
    title = (ticket.get("title") or "").strip().lower()
    return (priority, expected, updated_key, title)


def normalize_lab_name(value: Any) -> str:
    return clean_lab_name(value)


def resolve_group_lab(value: Any) -> str:
    normalized = normalize_lab_name(value)
    if not normalized:
        return DEFAULT_GROUP

    return LAB_ALIASES.get(normalize_lab_key(normalized), DEFAULT_GROUP)


def fetch_in_progress(db: firestore.Client) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {DEFAULT_GROUP: []}
    for lab in KNOWN_LABS:
        grouped.setdefault(lab, [])
    query = db.collection(TICKETS_COLLECTION).where("status", "==", "IN_PROGRESS").limit(500)

    for doc in query.stream():
        ticket = doc.to_dict() or {}
        lab_value = ticket.get("lab") or ticket.get("labKey")
        source_lab = canonical_lab_name(lab_value, KNOWN_LABS)
        group_lab = resolve_group_lab(lab_value)
        grouped.setdefault(group_lab, [])
        grouped[group_lab].append(
            {
                "id": doc.id,
                "shortId": doc.id[:8],
                "title": (ticket.get("title") or "").strip(),
                "lab": source_lab or DEFAULT_GROUP,
                "priority": (ticket.get("priority") or "").strip(),
                "category": (ticket.get("category") or "").strip(),
                "expectedDeliveryDate": (ticket.get("expectedDeliveryDate") or "").strip(),
                "updatedAt": (parse_timestamp(ticket.get("updatedAt")) or parse_timestamp(ticket.get("createdAt")) or dt.datetime.now(dt.timezone.utc)).isoformat(),
            }
        )

    for items in grouped.values():
        items.sort(key=ticket_sort_key)

    return grouped


def build_group_list(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    ordered_labs: list[str] = [DEFAULT_GROUP]

    for lab in KNOWN_LABS:
        if lab not in ordered_labs:
            ordered_labs.append(lab)

    for lab in sorted(grouped):
        if lab not in ordered_labs:
            ordered_labs.append(lab)

    return [{"lab": lab, "items": grouped.get(lab, [])} for lab in ordered_labs]


def patch_indicator(creds: service_account.Credentials, payload: dict[str, Any]) -> None:
    creds.refresh(Request())
    response = requests.patch(
        f"{RTDB_URL}/indicator.json",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"RTDB error {response.status_code}: {response.text}")


def main() -> None:
    creds = load_credentials()
    db = db_client(creds)
    groups = fetch_in_progress(db)
    group_list = build_group_list(groups)
    total = sum(len(items) for items in groups.values())

    payload = {
        "in_progress_groups": group_list,
        "in_progress_total": total,
        "in_progress_synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    patch_indicator(creds, payload)
    print(f"OK: synced {total} IN_PROGRESS ticket(s) across {len(groups)} lab(s)")


if __name__ == "__main__":
    main()
