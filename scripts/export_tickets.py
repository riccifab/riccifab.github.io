#!/usr/bin/env python3
"""Export tickets and comments from Firestore to JSON/CSV for analysis."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from google.cloud import firestore
    from google.oauth2 import service_account
except ModuleNotFoundError as exc:
    missing = exc.name or "google"
    raise SystemExit(
        "Missing Python dependency "
        f"'{missing}'. Install the script requirements first with:\n"
        f"  {sys.executable} -m pip install -r scripts/requirements.txt"
    ) from exc


def load_db() -> firestore.Client:
    project_id = os.environ["FIREBASE_PROJECT_ID"]
    sa_b64 = os.environ["GCP_SA_KEY_B64"]
    info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=project_id, credentials=creds)


def parse_timestamp(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat()

    if hasattr(value, "to_datetime"):
        try:
            converted = value.to_datetime()
        except Exception:
            converted = None
        if isinstance(converted, dt.datetime):
            if converted.tzinfo is None:
                converted = converted.replace(tzinfo=dt.timezone.utc)
            return converted.astimezone(dt.timezone.utc).isoformat()

    if isinstance(value, dict) and "seconds" in value:
        try:
            seconds = float(value.get("seconds"))
            nanos = float(value.get("nanoseconds", 0))
            return dt.datetime.fromtimestamp(seconds + nanos / 1e9, tz=dt.timezone.utc).isoformat()
        except Exception:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)

    if isinstance(value, list):
        return json.dumps([normalize_value(v) for v in value], ensure_ascii=False, sort_keys=True)

    if isinstance(value, dict):
        return json.dumps({k: normalize_value(v) for k, v in value.items()}, ensure_ascii=False, sort_keys=True)

    return str(value)


def normalize_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dt.datetime) or hasattr(value, "to_datetime"):
        return parse_timestamp(value)

    if isinstance(value, list):
        return [normalize_value(v) for v in value]

    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}

    return str(value)


def flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            child_prefix = f"{prefix}_{key}" if prefix else str(key)
            flatten(child_prefix, nested, out)
        return

    if isinstance(value, list):
        out[prefix] = json.dumps([normalize_value(v) for v in value], ensure_ascii=False, sort_keys=True)
        return

    out[prefix] = normalize_value(value)


def collect_export(db: firestore.Client, include_comments: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tickets: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []

    for snap in db.collection("tickets").stream():
        data = normalize_value(snap.to_dict() or {})
        ticket = {"id": snap.id, **data}
        tickets.append(ticket)

        if include_comments:
            for comment_snap in db.collection("tickets").document(snap.id).collection("comments").stream():
                comment = normalize_value(comment_snap.to_dict() or {})
                comments.append(
                    {
                        "ticket_id": snap.id,
                        "comment_id": comment_snap.id,
                        **comment,
                    }
                )

    tickets.sort(key=lambda row: (row.get("updatedAt") or row.get("createdAt") or "", row["id"]))
    comments.sort(key=lambda row: (row.get("createdAt") or "", row["ticket_id"], row["comment_id"]))
    return tickets, comments


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    flattened_rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    seen: set[str] = set()

    for row in rows:
        flat: dict[str, Any] = {}
        for key, value in row.items():
            flatten(str(key), value, flat)
        flattened_rows.append(flat)
        for key in flat:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in flattened_rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def default_output_dir() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("exports") / f"tickets-{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(default_output_dir()))
    parser.add_argument("--no-comments", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = load_db()
    tickets, comments = collect_export(db, include_comments=not args.no_comments)

    write_json(out_dir / "tickets.json", tickets)
    write_csv(out_dir / "tickets.csv", tickets)

    if not args.no_comments:
        write_json(out_dir / "comments.json", comments)
        write_csv(out_dir / "comments.csv", comments)

    summary = {
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tickets": len(tickets),
        "comments": len(comments),
        "out_dir": str(out_dir),
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
