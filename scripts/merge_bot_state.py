#!/usr/bin/env python3
"""Merge workflow state files into the bot-state checkout safely."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


LAST_RUN_FILES = {
    "last_run.json": "last_run_iso",
    "last_run_updates.json": "last_run",
}
TIMESTAMP_KEYS = ("updatedAt", "lastUpdatedAt", "modifiedAt", "lastModifiedAt", "createdAt")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_timestamp(value: Any) -> dt.datetime | None:
    if value is None:
        return None

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000.0
        return dt.datetime.fromtimestamp(raw, tz=dt.timezone.utc)

    if isinstance(value, dict) and "seconds" in value:
        try:
            seconds = float(value.get("seconds"))
            nanos = float(value.get("nanoseconds", 0))
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
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)

    return None


def iso_or_default(value: dt.datetime | None, fallback: str) -> str:
    if value is None:
        return fallback
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def merge_last_run_file(source_dir: Path, target_dir: Path, filename: str, key: str) -> None:
    source = read_json(source_dir / filename, {})
    target = read_json(target_dir / filename, {})

    merged_dt = max(
        parse_timestamp(source.get(key)) or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
        parse_timestamp(target.get(key)) or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
    )

    write_json(target_dir / filename, {key: iso_or_default(merged_dt, "1970-01-01T00:00:00Z")})


def snapshot_timestamp(snapshot: Any) -> dt.datetime | None:
    if not isinstance(snapshot, dict):
        return None
    for key in TIMESTAMP_KEYS:
        ts = parse_timestamp(snapshot.get(key))
        if ts is not None:
            return ts
    return None


def merge_snapshots(source_dir: Path, target_dir: Path) -> None:
    source = read_json(source_dir / "ticket_snapshots.json", {})
    target = read_json(target_dir / "ticket_snapshots.json", {})

    if not isinstance(source, dict):
        source = {}
    if not isinstance(target, dict):
        target = {}

    merged: dict[str, Any] = dict(target)

    for ticket_id, source_snapshot in source.items():
        if ticket_id not in merged:
            merged[ticket_id] = source_snapshot
            continue

        target_snapshot = merged[ticket_id]
        source_ts = snapshot_timestamp(source_snapshot)
        target_ts = snapshot_timestamp(target_snapshot)

        if source_ts and target_ts:
            merged[ticket_id] = source_snapshot if source_ts >= target_ts else target_snapshot
        elif source_ts and not target_ts:
            merged[ticket_id] = source_snapshot
        elif not source_ts and target_ts:
            merged[ticket_id] = target_snapshot
        else:
            merged[ticket_id] = source_snapshot

    write_json(target_dir / "ticket_snapshots.json", merged)


def merge_notified_ticket_ids(source_dir: Path, target_dir: Path) -> None:
    filename = "notified_ticket_ids.json"
    source = read_json(source_dir / filename, {})
    target = read_json(target_dir / filename, {})
    source_ids = source.get("ticket_ids", []) if isinstance(source, dict) else []
    target_ids = target.get("ticket_ids", []) if isinstance(target, dict) else []
    merged = sorted({str(value).strip() for value in [*source_ids, *target_ids] if str(value).strip()})
    write_json(target_dir / filename, {"ticket_ids": merged})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="state")
    parser.add_argument("--target-dir", default="bot-state/state")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for filename, key in LAST_RUN_FILES.items():
        if (source_dir / filename).exists() or (target_dir / filename).exists():
            merge_last_run_file(source_dir, target_dir, filename, key)

    if (source_dir / "ticket_snapshots.json").exists() or (target_dir / "ticket_snapshots.json").exists():
        merge_snapshots(source_dir, target_dir)

    if (source_dir / "notified_ticket_ids.json").exists() or (target_dir / "notified_ticket_ids.json").exists():
        merge_notified_ticket_ids(source_dir, target_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
