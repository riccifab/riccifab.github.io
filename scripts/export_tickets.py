#!/usr/bin/env python3
"""Export tickets and comments from Firestore to JSON/CSV for analysis."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
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


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID_PATTERN = re.compile(r'projectId:\s*"([^"]+)"')


def load_local_env() -> None:
    for path in (REPO_ROOT / ".env", REPO_ROOT / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value


def detect_project_id() -> str:
    env_value = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    if env_value:
        return env_value

    for path in (REPO_ROOT / "tickets.js", REPO_ROOT / "work_status.html"):
        if not path.exists():
            continue
        match = PROJECT_ID_PATTERN.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)

    raise SystemExit(
        "Missing FIREBASE_PROJECT_ID. Set it in the environment or .env, "
        "or keep a Firebase config with projectId in tickets.js/work_status.html."
    )


def load_credentials(credentials_file: str | None = None) -> service_account.Credentials | None:
    if credentials_file:
        return service_account.Credentials.from_service_account_file(credentials_file)

    env_credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if env_credentials_file:
        return service_account.Credentials.from_service_account_file(env_credentials_file)

    sa_b64 = os.getenv("GCP_SA_KEY_B64", "").strip()
    if sa_b64:
        info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
        return service_account.Credentials.from_service_account_info(info)

    return None


def load_db(project_id: str, credentials_file: str | None = None) -> firestore.Client:
    creds = load_credentials(credentials_file=credentials_file)
    if creds is not None:
        return firestore.Client(project=project_id, credentials=creds)
    return firestore.Client(project=project_id)


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
    load_local_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(default_output_dir()))
    parser.add_argument("--no-comments", action="store_true")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--credentials-file", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project_id = (args.project_id or detect_project_id()).strip()
    db = load_db(project_id=project_id, credentials_file=(args.credentials_file or "").strip() or None)
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
