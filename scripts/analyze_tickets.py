#!/usr/bin/env python3
"""Analyze exported tickets and build charts, reports, and labticketstats page."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import shutil
import sys
from collections import Counter, defaultdict
from html import escape as html_escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lab_names import canonical_lab_name, normalize_lab_key


DATE_FMT = "%Y-%m-%d"
OPEN_STATUSES = {"NEW", "TRIAGE", "IN_PROGRESS", "WAITING", "WAITING_ON_PI", "WAITING_ON_PROCUREMENT", "BLOCKED"}
DONE_STATUSES = {"DONE", "CLOSED"}
EFFORT_SCALE = {"S": 1, "M": 2, "L": 3, "XL": 4}
TOP_KEYWORDS_DEFAULT = 12
KEYWORD_MIN_COUNT_DEFAULT = 2
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#./-]*")
ETA_BUCKETS = ("Overdue", "0-7d", "8-14d", "15-30d", "31d+", "No ETA")
AGE_BUCKETS = ("0-7d", "8-14d", "15-30d", "31-60d", "61d+", "Unknown")
CHART_COLORS = [
    "#2563eb",
    "#0f766e",
    "#f97316",
    "#8b5cf6",
    "#ec4899",
    "#14b8a6",
    "#eab308",
    "#ef4444",
    "#06b6d4",
    "#84cc16",
]
DISPLAY_TIMEZONE = ZoneInfo("Europe/Rome")
CARD_TONES = [
    "tone-emerald",
    "tone-sky",
    "tone-violet",
    "tone-rose",
    "tone-amber",
    "tone-cyan",
]
INSIGHT_TONES = {
    "Largest backlog": "tone-sky",
    "Most overdue request": "tone-rose",
    "Oldest open ticket": "tone-violet",
    "Comment hotspot": "tone-amber",
    "Older tickets note": "tone-cyan",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "con",
    "da",
    "dei",
    "del",
    "della",
    "delle",
    "di",
    "do",
    "done",
    "for",
    "from",
    "gli",
    "how",
    "i",
    "if",
    "il",
    "in",
    "is",
    "it",
    "la",
    "le",
    "lo",
    "needs",
    "nel",
    "nella",
    "no",
    "non",
    "of",
    "on",
    "or",
    "per",
    "please",
    "requested",
    "should",
    "sono",
    "su",
    "that",
    "the",
    "this",
    "ticket",
    "to",
    "un",
    "una",
    "with",
    "yes",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_STATS_HTML = REPO_ROOT / "labticketstats.html"
ROOT_STATS_ASSETS = REPO_ROOT / "assets" / "labticketstats"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir",
        help="Export directory produced by export_tickets.py. Defaults to the latest exports/tickets-* directory.",
    )
    parser.add_argument(
        "--out-dir",
        help="Directory where the analysis files will be written. Defaults to <input-dir>/analysis.",
    )
    parser.add_argument(
        "--top-keywords",
        type=int,
        default=TOP_KEYWORDS_DEFAULT,
        help=f"Top recurring keywords to keep per group (default: {TOP_KEYWORDS_DEFAULT}).",
    )
    parser.add_argument(
        "--keyword-min-count",
        type=int,
        default=KEYWORD_MIN_COUNT_DEFAULT,
        help=f"Minimum keyword frequency to include in the report (default: {KEYWORD_MIN_COUNT_DEFAULT}).",
    )
    return parser.parse_args()


def latest_export_dir(base_dir: Path) -> Path:
    candidates = [path for path in base_dir.glob("tickets-*") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError("No export directories found under exports/. Run scripts/export_tickets.py first.")
    return sorted(candidates)[-1]


def parse_timestamp(value: Any) -> dt.datetime | None:
    if value in (None, "", "-"):
        return None

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

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

    if isinstance(value, dict) and "seconds" in value:
        try:
            seconds = float(value["seconds"])
            nanos = float(value.get("nanoseconds", 0))
        except (TypeError, ValueError):
            return None
        return dt.datetime.fromtimestamp(seconds + nanos / 1e9, tz=dt.timezone.utc)

    return None


def parse_date(value: Any) -> dt.date | None:
    if value in (None, "", "-"):
        return None

    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value

    if isinstance(value, dt.datetime):
        return value.date()

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if len(raw) >= 10:
            raw = raw[:10]
        try:
            return dt.datetime.strptime(raw, DATE_FMT).date()
        except ValueError:
            return None

    return None


def format_display_timestamp(value: Any, *, timezone: ZoneInfo = DISPLAY_TIMEZONE) -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return "-"
    return parsed.astimezone(timezone).strftime("%d/%m/%Y, %H:%M")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iso_or_blank(value: dt.datetime | None) -> str:
    return value.astimezone(dt.timezone.utc).isoformat() if value else ""


def date_or_blank(value: dt.date | None) -> str:
    return value.isoformat() if value else ""


def safe_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def coalesce_text(*values: Any, default: str = "") -> str:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return default


def normalize_group(value: Any, default: str = "Unspecified") -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return default


def normalize_lab_group(value: Any, lab_key: Any = None, default: str = "Unspecified") -> str:
    return canonical_lab_name(value or lab_key) or default


def normalize_effort(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().upper()
    return raw if raw in EFFORT_SCALE else ""


def effort_delta(requested: str, current: str) -> int | None:
    if requested not in EFFORT_SCALE or current not in EFFORT_SCALE:
        return None
    return EFFORT_SCALE[current] - EFFORT_SCALE[requested]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def round_or_none(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    if math.isnan(value):
        return None
    return round(value, digits)


def compact_number(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.1f}"
    return str(value)


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("._-/+#")
        if len(token) < 3:
            continue
        if token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def eta_bucket(days_to_eta: int | None) -> str:
    if days_to_eta is None:
        return "No ETA"
    if days_to_eta < 0:
        return "Overdue"
    if days_to_eta <= 7:
        return "0-7d"
    if days_to_eta <= 14:
        return "8-14d"
    if days_to_eta <= 30:
        return "15-30d"
    return "31d+"


def age_bucket(age_days: int | None) -> str:
    if age_days is None:
        return "Unknown"
    if age_days <= 7:
        return "0-7d"
    if age_days <= 14:
        return "8-14d"
    if age_days <= 30:
        return "15-30d"
    if age_days <= 60:
        return "31-60d"
    return "61d+"


def week_start(date_value: dt.date) -> dt.date:
    return date_value - dt.timedelta(days=date_value.weekday())


def load_comments_by_ticket(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}

    comments_raw = load_json(path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comments_raw:
        if not isinstance(row, dict):
            continue
        ticket_id = str(row.get("ticket_id") or "").strip()
        if not ticket_id:
            continue
        grouped[ticket_id].append(row)

    for ticket_id, rows in grouped.items():
        rows.sort(key=lambda item: parse_timestamp(item.get("createdAt")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))

    return grouped


def analyze_tickets(
    tickets_raw: list[dict[str, Any]],
    comments_by_ticket: dict[str, list[dict[str, Any]]],
    *,
    top_keywords: int,
    keyword_min_count: int,
) -> dict[str, Any]:
    today = dt.datetime.now(dt.timezone.utc).date()
    current_week = week_start(today)

    enriched_rows: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    current_priority_counter: Counter[str] = Counter()
    requested_priority_counter: Counter[str] = Counter()
    open_priority_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    current_lab_counter: Counter[str] = Counter()
    requested_lab_counter: Counter[str] = Counter()
    effort_counter: Counter[str] = Counter()
    eta_bucket_counter: Counter[str] = Counter()
    age_bucket_counter: Counter[str] = Counter()
    created_week_counter: Counter[dt.date] = Counter()
    closed_week_counter: Counter[dt.date] = Counter()
    lab_summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tickets_total": 0,
            "open_tickets": 0,
            "done_tickets": 0,
            "overdue_open_tickets": 0,
            "median_ticket_age_days": [],
            "eta_shift_days": [],
            "effort_delta": [],
        }
    )
    category_summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tickets_total": 0,
            "open_tickets": 0,
            "done_tickets": 0,
            "overdue_open_tickets": 0,
            "median_ticket_age_days": [],
            "eta_shift_days": [],
            "effort_delta": [],
        }
    )

    effort_mismatches: list[dict[str, Any]] = []
    eta_changes: list[dict[str, Any]] = []
    overdue_open_tickets: list[dict[str, Any]] = []
    keyword_buckets: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    comment_counts: list[int] = []
    resolution_ages: list[float] = []
    lab_labels_by_key: dict[str, str] = {}

    def grouped_lab_name(value: Any, lab_key: Any = None) -> str:
        label = normalize_lab_group(value, lab_key)
        stable_key = normalize_lab_key(lab_key or value)
        if not stable_key:
            return label
        return lab_labels_by_key.setdefault(stable_key, label)

    for ticket in tickets_raw:
        if not isinstance(ticket, dict):
            continue

        ticket_id = str(ticket.get("id") or "").strip()
        if not ticket_id:
            continue

        requester = ticket.get("requesterSubmission") if isinstance(ticket.get("requesterSubmission"), dict) else {}

        status = normalize_group(ticket.get("status"), default="UNKNOWN")
        current_lab = grouped_lab_name(ticket.get("lab"), ticket.get("labKey"))
        requested_lab = grouped_lab_name(requester.get("lab"), requester.get("labKey"))
        category = normalize_group(ticket.get("category"))
        priority = normalize_group(ticket.get("priority"))
        requested_priority = normalize_group(requester.get("priority"))
        current_eta = parse_date(ticket.get("expectedDeliveryDate"))
        requested_eta = parse_date(requester.get("expectedDeliveryDate"))
        current_effort = normalize_effort(ticket.get("effortAdmin") or ticket.get("effortGuess"))
        requested_effort = normalize_effort(requester.get("effortGuess"))
        created_at = parse_timestamp(ticket.get("createdAt"))
        updated_at = parse_timestamp(ticket.get("updatedAt"))
        is_open = status in OPEN_STATUSES
        is_done = status in DONE_STATUSES
        age_days = (today - created_at.date()).days if created_at else None
        days_to_eta = (current_eta - today).days if current_eta else None
        requested_days_to_eta = (requested_eta - today).days if requested_eta else None
        eta_shift_days = (current_eta - requested_eta).days if current_eta and requested_eta else None
        current_eta_bucket = eta_bucket(days_to_eta) if is_open else ""
        current_age_bucket = age_bucket(age_days) if is_open else ""
        comment_rows = comments_by_ticket.get(ticket_id, [])
        comment_count = max(len(comment_rows), safe_int(ticket.get("commentCount")))
        last_comment_at = parse_timestamp(ticket.get("lastCommentAt"))
        if last_comment_at is None and comment_rows:
            last_comment_at = parse_timestamp(comment_rows[-1].get("createdAt"))

        status_counter[status] += 1
        current_priority_counter[priority] += 1
        requested_priority_counter[requested_priority] += 1
        category_counter[category] += 1
        current_lab_counter[current_lab] += 1
        requested_lab_counter[requested_lab] += 1
        effort_counter[current_effort or "Unspecified"] += 1
        comment_counts.append(comment_count)

        if created_at:
            created_week_counter[week_start(created_at.date())] += 1
        if is_done and updated_at:
            closed_week_counter[week_start(updated_at.date())] += 1

        lab_row = lab_summary[current_lab]
        lab_row["tickets_total"] += 1
        category_row = category_summary[category]
        category_row["tickets_total"] += 1

        if is_open:
            lab_row["open_tickets"] += 1
            category_row["open_tickets"] += 1
            open_priority_counter[priority] += 1
            eta_bucket_counter[current_eta_bucket] += 1
            age_bucket_counter[current_age_bucket] += 1
        if is_done:
            lab_row["done_tickets"] += 1
            category_row["done_tickets"] += 1
        if age_days is not None:
            lab_row["median_ticket_age_days"].append(age_days)
            category_row["median_ticket_age_days"].append(age_days)
        if eta_shift_days is not None:
            lab_row["eta_shift_days"].append(eta_shift_days)
            category_row["eta_shift_days"].append(eta_shift_days)
        delta = effort_delta(requested_effort, current_effort)
        if delta is not None:
            lab_row["effort_delta"].append(delta)
            category_row["effort_delta"].append(delta)

        if is_open and current_eta and current_eta < today:
            overdue_days = (today - current_eta).days
            lab_row["overdue_open_tickets"] += 1
            category_row["overdue_open_tickets"] += 1
            overdue_open_tickets.append(
                {
                    "ticket_id": ticket_id,
                    "title": coalesce_text(ticket.get("title")),
                    "lab": current_lab,
                    "category": category,
                    "status": status,
                    "priority": priority,
                    "eta": date_or_blank(current_eta),
                    "overdue_days": overdue_days,
                    "requester_eta": date_or_blank(requested_eta),
                    "creator_email": coalesce_text(ticket.get("creatorEmail"), ticket.get("createdByEmail")),
                }
            )

        if is_done and created_at and updated_at and updated_at >= created_at:
            resolution_ages.append((updated_at - created_at).total_seconds() / 86400)

        if eta_shift_days not in (None, 0):
            eta_changes.append(
                {
                    "ticket_id": ticket_id,
                    "title": coalesce_text(ticket.get("title")),
                    "lab": current_lab,
                    "category": category,
                    "status": status,
                    "requested_eta": date_or_blank(requested_eta),
                    "current_eta": date_or_blank(current_eta),
                    "eta_shift_days": eta_shift_days,
                    "days_to_eta": days_to_eta if days_to_eta is not None else "",
                }
            )

        if delta not in (None, 0):
            effort_mismatches.append(
                {
                    "ticket_id": ticket_id,
                    "title": coalesce_text(ticket.get("title")),
                    "lab": current_lab,
                    "category": category,
                    "status": status,
                    "requested_effort": requested_effort,
                    "current_effort": current_effort,
                    "effort_delta": delta,
                }
            )

        text_blob = " ".join(
            filter(
                None,
                [
                    coalesce_text(ticket.get("title")),
                    coalesce_text(ticket.get("description")),
                    coalesce_text(ticket.get("definitionOfDone")),
                    coalesce_text(requester.get("description")),
                    coalesce_text(requester.get("definitionOfDone")),
                    category,
                ],
            )
        )
        for token in tokenize(text_blob):
            keyword_buckets[("lab", current_lab)][token] += 1
            keyword_buckets[("category", category)][token] += 1

        enriched_rows.append(
            {
                "ticket_id": ticket_id,
                "title": coalesce_text(ticket.get("title")),
                "status": status,
                "category": category,
                "lab_current": current_lab,
                "lab_requested": requested_lab,
                "priority_current": priority,
                "priority_requested": requested_priority,
                "eta_current": date_or_blank(current_eta),
                "eta_requested": date_or_blank(requested_eta),
                "eta_shift_days": "" if eta_shift_days is None else eta_shift_days,
                "days_to_eta_current": "" if days_to_eta is None else days_to_eta,
                "days_to_eta_requested": "" if requested_days_to_eta is None else requested_days_to_eta,
                "eta_bucket_open": current_eta_bucket,
                "age_bucket_open": current_age_bucket,
                "overdue_open": "yes" if is_open and current_eta and current_eta < today else "no",
                "effort_current": current_effort or "Unspecified",
                "effort_requested": requested_effort,
                "effort_delta": "" if delta is None else delta,
                "commercially_available": coalesce_text(ticket.get("commerciallyAvailable"), default="unknown"),
                "procurement_needed": coalesce_text(ticket.get("procurementNeeded"), default="unknown"),
                "can_be_deferred": coalesce_text(ticket.get("canBeDeferred"), default="unknown"),
                "why_not_deferred_code": coalesce_text(ticket.get("whyNotDeferredCode")),
                "creator_email": coalesce_text(ticket.get("creatorEmail"), ticket.get("createdByEmail")),
                "assignee_email": coalesce_text(ticket.get("assigneeEmail")),
                "created_at": iso_or_blank(created_at),
                "updated_at": iso_or_blank(updated_at),
                "ticket_age_days": "" if age_days is None else age_days,
                "comment_count": comment_count,
                "last_comment_at": iso_or_blank(last_comment_at),
            }
        )

    def finalize_group_rows(source: dict[str, dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, values in source.items():
            rows.append(
                {
                    key_name: key,
                    "tickets_total": values["tickets_total"],
                    "open_tickets": values["open_tickets"],
                    "done_tickets": values["done_tickets"],
                    "overdue_open_tickets": values["overdue_open_tickets"],
                    "median_ticket_age_days": round_or_none(median(values.get("median_ticket_age_days", []))),
                    "average_eta_shift_days": round_or_none(average(values.get("eta_shift_days", []))),
                    "average_effort_delta": round_or_none(average(values.get("effort_delta", []))),
                }
            )
        return rows

    lab_summary_rows = sorted(
        finalize_group_rows(lab_summary, "lab"),
        key=lambda row: (safe_int(row["open_tickets"]), safe_int(row["tickets_total"])),
        reverse=True,
    )
    category_summary_rows = sorted(
        finalize_group_rows(category_summary, "category"),
        key=lambda row: (safe_int(row["tickets_total"]), safe_int(row["open_tickets"])),
        reverse=True,
    )

    priority_keys = sorted(set(current_priority_counter) | set(requested_priority_counter))
    priority_summary_rows = [
        {
            "priority": key,
            "current_count": current_priority_counter.get(key, 0),
            "requested_count": requested_priority_counter.get(key, 0),
        }
        for key in priority_keys
    ]

    open_priority_summary_rows = [
        {"priority": key, "open_count": open_priority_counter.get(key, 0)}
        for key in ["P0", "P1", "P2", "P3"]
        if open_priority_counter.get(key, 0) > 0
    ]

    status_summary_rows = sorted(
        [{"status": key, "count": count} for key, count in status_counter.items()],
        key=lambda row: (safe_int(row["count"]), row["status"]),
        reverse=True,
    )
    lab_distribution_rows = sorted(
        [{"lab": key, "count": count} for key, count in current_lab_counter.items()],
        key=lambda row: (safe_int(row["count"]), row["lab"]),
        reverse=True,
    )
    category_distribution_rows = sorted(
        [{"category": key, "count": count} for key, count in category_counter.items()],
        key=lambda row: (safe_int(row["count"]), row["category"]),
        reverse=True,
    )
    effort_distribution_rows = sorted(
        [{"effort": key, "count": count} for key, count in effort_counter.items()],
        key=lambda row: (safe_int(row["count"]), row["effort"]),
        reverse=True,
    )
    open_eta_bucket_rows = [{"bucket": bucket, "count": eta_bucket_counter.get(bucket, 0)} for bucket in ETA_BUCKETS]
    open_age_bucket_rows = [{"bucket": bucket, "count": age_bucket_counter.get(bucket, 0)} for bucket in AGE_BUCKETS]

    skill_signal_rows: list[dict[str, Any]] = []
    for (group_type, group_value), counter in sorted(keyword_buckets.items()):
        kept = 0
        for keyword, count in counter.most_common():
            if count < keyword_min_count:
                continue
            skill_signal_rows.append(
                {
                    "group_type": group_type,
                    "group_value": group_value,
                    "keyword": keyword,
                    "count": count,
                }
            )
            kept += 1
            if kept >= top_keywords:
                break

    top_commented_rows = sorted(
        [
            {
                "ticket_id": row["ticket_id"],
                "title": row["title"],
                "status": row["status"],
                "lab": row["lab_current"],
                "comment_count": safe_int(row["comment_count"]),
                "updated_at": row["updated_at"],
            }
            for row in enriched_rows
            if safe_int(row["comment_count"]) > 0
        ],
        key=lambda row: (row["status"] in OPEN_STATUSES, row["comment_count"], row["updated_at"], row["ticket_id"]),
        reverse=True,
    )

    timeline_points: list[dt.date] = sorted(set(created_week_counter) | set(closed_week_counter))
    if timeline_points:
        cursor = min(timeline_points)
    else:
        cursor = current_week
    last_week = max(timeline_points + [current_week]) if timeline_points else current_week

    timeline_rows: list[dict[str, Any]] = []
    cumulative_created = 0
    cumulative_closed = 0
    while cursor <= last_week:
        created_count = created_week_counter.get(cursor, 0)
        closed_count = closed_week_counter.get(cursor, 0)
        cumulative_created += created_count
        cumulative_closed += closed_count
        timeline_rows.append(
            {
                "week": cursor.isoformat(),
                "label": cursor.strftime("%d %b"),
                "created": cumulative_created,
                "closed": cumulative_closed,
                "open": max(cumulative_created - cumulative_closed, 0),
            }
        )
        cursor += dt.timedelta(days=7)

    priority_changes = [
        row
        for row in enriched_rows
        if row["priority_requested"] not in ("", "Unspecified")
        and row["priority_current"] not in ("", "Unspecified")
        and row["priority_requested"] != row["priority_current"]
    ]

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tickets_total": len(enriched_rows),
        "open_tickets": sum(1 for row in enriched_rows if row["status"] in OPEN_STATUSES),
        "done_tickets": sum(1 for row in enriched_rows if row["status"] in DONE_STATUSES),
        "overdue_open_tickets": len(overdue_open_tickets),
        "tickets_with_requester_snapshot": sum(
            1 for ticket in tickets_raw if isinstance(ticket.get("requesterSubmission"), dict) and ticket.get("requesterSubmission")
        ),
        "tickets_with_comments": sum(1 for row in enriched_rows if safe_int(row["comment_count"]) > 0),
        "average_comment_count": round_or_none(average([float(count) for count in comment_counts])),
        "median_comment_count": round_or_none(median([float(count) for count in comment_counts])),
        "approx_resolution_days_average": round_or_none(average(resolution_ages)),
        "approx_resolution_days_median": round_or_none(median(resolution_ages)),
        "priority_changes": len(priority_changes),
        "effort_changes": len(effort_mismatches),
        "eta_changes": len(eta_changes),
        "top_labs": [(row["lab"], row["count"]) for row in lab_distribution_rows[:5]],
        "top_categories": [(row["category"], row["count"]) for row in category_distribution_rows[:5]],
    }

    open_rows = [row for row in enriched_rows if row["status"] in OPEN_STATUSES]
    insights: list[dict[str, str]] = []

    if lab_summary_rows and safe_int(lab_summary_rows[0]["open_tickets"]) > 0:
        row = lab_summary_rows[0]
        insights.append(
            {
                "title": "Largest backlog",
                "detail": f"{row['lab']} holds {row['open_tickets']} open tickets out of {row['tickets_total']} total.",
            }
        )

    if overdue_open_tickets:
        row = max(overdue_open_tickets, key=lambda item: safe_int(item["overdue_days"]))
        insights.append(
            {
                "title": "Most overdue request",
                "detail": f"#{row['ticket_id'][:8]} in {row['lab']} is late by {row['overdue_days']} days ({row['title']}).",
            }
        )

    if open_rows:
        row = max(open_rows, key=lambda item: safe_int(item["ticket_age_days"]))
        insights.append(
            {
                "title": "Oldest open ticket",
                "detail": f"#{row['ticket_id'][:8]} has been open for {row['ticket_age_days']} days ({row['title']}).",
            }
        )

    if top_commented_rows:
        row = next((item for item in top_commented_rows if item["status"] in OPEN_STATUSES), top_commented_rows[0])
        insights.append(
            {
                "title": "Comment hotspot",
                "detail": f"#{row['ticket_id'][:8]} collected {row['comment_count']} comments and is in status {row['status']}.",
            }
        )

    if summary["tickets_with_requester_snapshot"] == 0:
        insights.append(
            {
                "title": "Older tickets note",
                "detail": "These tickets were created before we started saving the original request separately, so requested-vs-final effort and ETA comparisons are still limited here.",
            }
        )

    return {
        "summary": summary,
        "insights": insights,
        "status_summary_rows": status_summary_rows,
        "priority_summary_rows": priority_summary_rows,
        "open_priority_summary_rows": open_priority_summary_rows,
        "lab_summary_rows": lab_summary_rows,
        "category_summary_rows": category_summary_rows,
        "lab_distribution_rows": lab_distribution_rows,
        "category_distribution_rows": category_distribution_rows,
        "effort_distribution_rows": effort_distribution_rows,
        "open_eta_bucket_rows": open_eta_bucket_rows,
        "open_age_bucket_rows": open_age_bucket_rows,
        "timeline_rows": timeline_rows,
        "top_commented_rows": top_commented_rows,
        "effort_mismatches": sorted(effort_mismatches, key=lambda row: (row["lab"], row["category"], row["ticket_id"])),
        "eta_changes": sorted(eta_changes, key=lambda row: (row["eta_shift_days"], row["ticket_id"]), reverse=True),
        "overdue_open_tickets": sorted(
            overdue_open_tickets, key=lambda row: (row["overdue_days"], row["priority"], row["ticket_id"]), reverse=True
        ),
        "skill_signal_rows": skill_signal_rows,
        "enriched_rows": sorted(enriched_rows, key=lambda row: (row["status"], row["lab_current"], row["ticket_id"])),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No data._"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = ["" if row.get(col) is None else str(row.get(col, "")) for col in columns]
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider, *body])


def color_for_index(index: int) -> str:
    return CHART_COLORS[index % len(CHART_COLORS)]


def compress_rows_for_pie(rows: list[dict[str, Any]], label_key: str, value_key: str, *, max_items: int = 6) -> list[dict[str, Any]]:
    filtered = [row for row in rows if safe_float(row.get(value_key)) > 0]
    if len(filtered) <= max_items:
        return filtered
    kept = filtered[: max_items - 1]
    other_total = sum(safe_float(row.get(value_key)) for row in filtered[max_items - 1 :])
    kept.append({label_key: "Other", value_key: other_total})
    return kept


def polar_to_cartesian(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    angle_rad = math.radians(angle_deg - 90)
    return (cx + radius * math.cos(angle_rad), cy + radius * math.sin(angle_rad))


def donut_segment_path(cx: float, cy: float, outer_r: float, inner_r: float, start_angle: float, end_angle: float) -> str:
    if end_angle - start_angle >= 359.999:
        return (
            f"M {cx} {cy - outer_r} "
            f"A {outer_r} {outer_r} 0 1 1 {cx - 0.01} {cy - outer_r} "
            f"L {cx - 0.01} {cy - inner_r} "
            f"A {inner_r} {inner_r} 0 1 0 {cx} {cy - inner_r} Z"
        )

    outer_start = polar_to_cartesian(cx, cy, outer_r, start_angle)
    outer_end = polar_to_cartesian(cx, cy, outer_r, end_angle)
    inner_end = polar_to_cartesian(cx, cy, inner_r, end_angle)
    inner_start = polar_to_cartesian(cx, cy, inner_r, start_angle)
    large_arc = 1 if (end_angle - start_angle) > 180 else 0
    return (
        f"M {outer_start[0]:.3f} {outer_start[1]:.3f} "
        f"A {outer_r} {outer_r} 0 {large_arc} 1 {outer_end[0]:.3f} {outer_end[1]:.3f} "
        f"L {inner_end[0]:.3f} {inner_end[1]:.3f} "
        f"A {inner_r} {inner_r} 0 {large_arc} 0 {inner_start[0]:.3f} {inner_start[1]:.3f} Z"
    )


def render_donut_chart_svg(
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
    *,
    subtitle: str = "",
    width: int = 900,
    height: int = 520,
    max_items: int = 6,
) -> str:
    rows = compress_rows_for_pie(rows, label_key, value_key, max_items=max_items)
    total = sum(safe_float(row.get(value_key)) for row in rows)
    cx, cy = 252, 280
    outer_r = 168
    inner_r = 96

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" rx="28" fill="#f8fafc" />',
    ]

    if total <= 0:
        lines.append(f'<circle cx="{cx}" cy="{cy}" r="{outer_r}" fill="#e2e8f0" />')
        lines.append(f'<circle cx="{cx}" cy="{cy}" r="{inner_r}" fill="#f8fafc" />')
    else:
        angle = 0.0
        for index, row in enumerate(rows):
            value = safe_float(row.get(value_key))
            slice_angle = 360 * value / total
            path = donut_segment_path(cx, cy, outer_r, inner_r, angle, angle + slice_angle)
            lines.append(f'<path d="{path}" fill="{color_for_index(index)}" />')
            angle += slice_angle

    lines.extend(
        [
            f'<circle cx="{cx}" cy="{cy}" r="{inner_r - 2}" fill="#ffffff" />',
            f'<text x="{cx}" y="{cy - 12}" text-anchor="middle" fill="#64748b" font-size="15" font-family="Arial, sans-serif">Total</text>',
            f'<text x="{cx}" y="{cy + 24}" text-anchor="middle" fill="#0f172a" font-size="40" font-family="Arial, sans-serif" font-weight="700">{int(total) if total.is_integer() else total:.0f}</text>',
        ]
    )

    legend_x = 500
    legend_y = 118
    gap = 60
    for index, row in enumerate(rows):
        value = safe_float(row.get(value_key))
        label = str(row.get(label_key, "") or "-")
        pct = 0 if total <= 0 else value / total * 100
        y = legend_y + index * gap
        lines.extend(
            [
                f'<circle cx="{legend_x}" cy="{y}" r="10" fill="{color_for_index(index)}" />',
                f'<text x="{legend_x + 20}" y="{y - 4}" fill="#0f172a" font-size="16" font-family="Arial, sans-serif" font-weight="600">{html_escape(label)}</text>',
                f'<text x="{legend_x + 20}" y="{y + 20}" fill="#475569" font-size="14" font-family="Arial, sans-serif">{html_escape(compact_number(value))} tickets • {pct:.0f}%</text>',
            ]
        )

    lines.append("</svg>")
    return "\n".join(lines)


def render_horizontal_bar_chart_svg(
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
    *,
    subtitle: str = "",
    bar_color: str = "#2563eb",
    width: int = 920,
    max_rows: int = 10,
) -> str:
    rows = [row for row in rows if safe_float(row.get(value_key)) > 0][:max_rows]
    if not rows:
        rows = [{label_key: "No data", value_key: 0}]

    labels = [str(row.get(label_key, "") or "-") for row in rows]
    values = [safe_float(row.get(value_key)) for row in rows]
    max_value = max(values) if values else 0.0
    left_pad = min(340, max(168, max(len(label) for label in labels) * 8))
    right_pad = 74
    top_pad = 34
    bar_height = 28
    gap = 20
    bottom_pad = 24
    chart_width = width - left_pad - right_pad
    height = top_pad + len(rows) * (bar_height + gap) + bottom_pad

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" rx="28" fill="#f8fafc" />',
    ]

    for index, row in enumerate(rows):
        label = str(row.get(label_key, "") or "-")
        value = safe_float(row.get(value_key))
        y = top_pad + index * (bar_height + gap)
        bar_width = 0 if max_value <= 0 else (value / max_value) * chart_width
        lines.extend(
            [
                f'<text x="24" y="{y + 19}" fill="#0f172a" font-size="14" font-family="Arial, sans-serif">{html_escape(label)}</text>',
                f'<rect x="{left_pad}" y="{y}" width="{chart_width}" height="{bar_height}" rx="10" fill="#e2e8f0" />',
                f'<rect x="{left_pad}" y="{y}" width="{bar_width:.1f}" height="{bar_height}" rx="10" fill="{bar_color}" />',
                f'<text x="{left_pad + chart_width + 10}" y="{y + 19}" fill="#0f172a" font-size="14" font-family="Arial, sans-serif" font-weight="700">{html_escape(compact_number(value))}</text>',
            ]
        )

    lines.append("</svg>")
    return "\n".join(lines)


def render_line_chart_svg(
    title: str,
    rows: list[dict[str, Any]],
    *,
    subtitle: str = "",
    width: int = 1040,
    height: int = 520,
) -> str:
    if not rows:
        rows = [{"label": "Now", "created": 0, "closed": 0, "open": 0}]

    series = [
        ("created", "Created", "#2563eb"),
        ("closed", "Closed", "#10b981"),
        ("open", "Open", "#f97316"),
    ]
    raw_max = max([1.0] + [safe_float(row.get(key)) for row in rows for key, _, _ in series])
    tick_step = max(1, math.ceil(raw_max / 4))
    axis_max = tick_step * 4
    left_pad = 60
    right_pad = 154
    top_pad = 34
    bottom_pad = 58
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    step_x = chart_width / max(1, len(rows) - 1)

    def point(index: int, value: float) -> tuple[float, float]:
        x = left_pad + (0 if len(rows) == 1 else index * step_x)
        y = top_pad + chart_height - (value / axis_max) * chart_height
        return (x, y)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" rx="28" fill="#f8fafc" />',
    ]

    for tick in range(5):
        value = tick_step * tick
        y = top_pad + chart_height - (value / axis_max) * chart_height
        lines.append(f'<line x1="{left_pad}" y1="{y:.1f}" x2="{width - right_pad}" y2="{y:.1f}" stroke="#dbe4f0" stroke-width="1" />')
        lines.append(
            f'<text x="{left_pad - 12}" y="{y + 4:.1f}" text-anchor="end" fill="#64748b" font-size="12" font-family="Arial, sans-serif">{html_escape(compact_number(value))}</text>'
        )

    x_label_step = max(1, math.ceil(len(rows) / 8))
    for index, row in enumerate(rows):
        if index % x_label_step != 0 and index != len(rows) - 1:
            continue
        x = left_pad + (0 if len(rows) == 1 else index * step_x)
        lines.append(f'<text x="{x:.1f}" y="{height - 18}" text-anchor="middle" fill="#64748b" font-size="12" font-family="Arial, sans-serif">{html_escape(str(row.get("label", "")))}</text>')

    for key, _, color in series:
        points = [point(index, safe_float(row.get(key))) for index, row in enumerate(rows)]
        polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="{polyline}" />')
        for x, y in points:
            lines.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" fill="{color}" stroke="#fff" stroke-width="2" />')

    end_labels = []
    for key, label, color in series:
        last_value = safe_float(rows[-1].get(key))
        _, last_y = point(len(rows) - 1, last_value)
        end_labels.append({"label": label, "value": compact_number(last_value), "color": color, "y": last_y})

    end_labels.sort(key=lambda item: item["y"])
    min_gap = 28
    for index in range(1, len(end_labels)):
        if end_labels[index]["y"] - end_labels[index - 1]["y"] < min_gap:
            end_labels[index]["y"] = end_labels[index - 1]["y"] + min_gap

    label_x = width - 136
    for item in end_labels:
        y = max(48, min(height - 36, item["y"]))
        lines.append(f'<rect x="{label_x}" y="{y - 13:.1f}" width="112" height="26" rx="13" fill="#ffffff" stroke="{item["color"]}" stroke-width="1.5" />')
        lines.append(f'<circle cx="{label_x + 14}" cy="{y:.1f}" r="5" fill="{item["color"]}" />')
        lines.append(
            f'<text x="{label_x + 26}" y="{y + 4:.1f}" fill="#0f172a" font-size="12" font-family="Arial, sans-serif" font-weight="700">'
            f'{html_escape(item["label"] + " " + item["value"])}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def trim_ticket_label(ticket_id: str, title: str, limit: int = 30) -> str:
    title = coalesce_text(title, default="-")
    trimmed = title if len(title) <= limit else title[: limit - 1].rstrip() + "…"
    return f"#{ticket_id[:6]} {trimmed}"


def write_plot_svgs(plots_dir: Path, analysis: dict[str, Any]) -> list[dict[str, Any]]:
    plots_dir.mkdir(parents=True, exist_ok=True)

    top_comment_chart_rows = [
        {"label": trim_ticket_label(row["ticket_id"], row["title"]), "count": row["comment_count"]}
        for row in analysis["top_commented_rows"][:8]
    ]
    open_by_lab_rows = [
        {"label": row["lab"], "count": row["open_tickets"]}
        for row in analysis["lab_summary_rows"]
        if safe_int(row["open_tickets"]) > 0
    ]

    chart_specs = [
        {
            "file": "labs_share.svg",
            "title": "Tickets by lab",
            "subtitle": "How the full ticket load is split across labs.",
            "span": "half",
            "svg": render_donut_chart_svg(
                "Tickets by lab",
                analysis["lab_distribution_rows"],
                "lab",
                "count",
                subtitle="Current snapshot across all tickets.",
            ),
        },
        {
            "file": "categories_share.svg",
            "title": "Tickets by category",
            "subtitle": "Which kinds of requests arrive most often.",
            "span": "half",
            "svg": render_donut_chart_svg(
                "Tickets by category",
                analysis["category_distribution_rows"],
                "category",
                "count",
                subtitle="Total requests by category.",
            ),
        },
        {
            "file": "effort_share.svg",
            "title": "Tickets by effort",
            "subtitle": "Current effort sizing, including tickets still unsized.",
            "span": "half",
            "svg": render_donut_chart_svg(
                "Tickets by effort",
                analysis["effort_distribution_rows"],
                "effort",
                "count",
                subtitle="Admin effort if present, otherwise requester effort.",
            ),
        },
        {
            "file": "status_share.svg",
            "title": "Tickets by status",
            "subtitle": "Where the current workload sits in the pipeline.",
            "span": "half",
            "svg": render_donut_chart_svg(
                "Tickets by status",
                analysis["status_summary_rows"],
                "status",
                "count",
                subtitle="How tickets are distributed across statuses.",
                max_items=7,
            ),
        },
        {
            "file": "ticket_flow_timeline.svg",
            "title": "Created, closed, and open",
            "subtitle": "Cumulative totals over time, so the latest point matches the current snapshot.",
            "span": "wide",
            "svg": render_line_chart_svg(
                "Created, closed, and open",
                analysis["timeline_rows"],
                subtitle="The closed line uses the latest ticket update as the best available close date.",
            ),
        },
        {
            "file": "open_priority.svg",
            "title": "Open tickets by priority",
            "subtitle": "How urgent the current open queue is by assigned priority.",
            "span": "half",
            "svg": render_horizontal_bar_chart_svg(
                "Open tickets by priority",
                analysis["open_priority_summary_rows"],
                "priority",
                "open_count",
                subtitle="Only tickets still open.",
                bar_color="#dc2626",
            ),
        },
        {
            "file": "comments_hotspots.svg",
            "title": "Most commented tickets",
            "subtitle": "Tickets that are generating the most back-and-forth.",
            "span": "half",
            "svg": render_horizontal_bar_chart_svg(
                "Most commented tickets",
                top_comment_chart_rows,
                "label",
                "count",
                subtitle="High-comment tickets are often process bottlenecks.",
                bar_color="#be185d",
                max_rows=8,
            ),
        },
        {
            "file": "open_eta_buckets.svg",
            "title": "Open tickets by urgency",
            "subtitle": "How many open tickets are overdue, near-term, or further out.",
            "span": "half",
            "svg": render_horizontal_bar_chart_svg(
                "Open tickets by urgency",
                analysis["open_eta_bucket_rows"],
                "bucket",
                "count",
                subtitle="Overdue and near-term tickets stand out here.",
                bar_color="#ea580c",
            ),
        },
        {
            "file": "open_by_lab.svg",
            "title": "Labs with the most open tickets",
            "subtitle": "Where the unfinished workload is currently concentrated.",
            "span": "half",
            "svg": render_horizontal_bar_chart_svg(
                "Labs with the most open tickets",
                open_by_lab_rows,
                "label",
                "count",
                subtitle="Which labs are carrying the heaviest open load.",
                bar_color="#2563eb",
            ),
        },
    ]

    for spec in chart_specs:
        (plots_dir / spec["file"]).write_text(spec["svg"], encoding="utf-8")

    return [{"file": spec["file"], "title": spec["title"], "subtitle": spec["subtitle"], "span": spec["span"]} for spec in chart_specs]


def render_rank_list(title: str, rows: list[dict[str, Any]], formatter) -> str:
    if not rows:
        return f'<section class="list-card"><h3>{html_escape(title)}</h3><p class="muted">No data.</p></section>'
    items = "".join(f"<li>{formatter(row)}</li>" for row in rows)
    return f'<section class="list-card"><h3>{html_escape(title)}</h3><ol>{items}</ol></section>'


def tone_class(index: int) -> str:
    return CARD_TONES[index % len(CARD_TONES)]


def insight_tone(title: str, index: int) -> str:
    return INSIGHT_TONES.get(title, CARD_TONES[index % len(CARD_TONES)])


def render_insights(insights: list[dict[str, str]]) -> str:
    if not insights:
        return '<section class="insights"><div class="insight tone-cyan"><h3>No standout findings</h3><p>The snapshot looks fairly balanced.</p></div></section>'
    cards = "".join(
        f'<div class="insight {insight_tone(item["title"], index)}"><h3>{html_escape(item["title"])}</h3><p>{html_escape(item["detail"])}</p></div>'
        for index, item in enumerate(insights)
    )
    return f'<section class="insights">{cards}</section>'


def render_stats_html(
    *,
    page_title: str,
    input_dir: Path,
    out_dir: Path,
    analysis: dict[str, Any],
    plot_manifest: list[dict[str, Any]],
    plot_base: str,
    root_mode: bool,
) -> str:
    summary = analysis["summary"]
    source_label = input_dir.name if root_mode else str(input_dir)
    output_label = f"{input_dir.name}/analysis" if root_mode else str(out_dir)
    generated_label = format_display_timestamp(summary["generated_at"])
    hero_links = [
        ("Back to home", "index.html" if root_mode else "../../../index.html"),
        ("Ticketing portal", "tickets.html" if root_mode else "../../../tickets.html"),
        ("Work status", "work_status.html" if root_mode else "../../../work_status.html"),
    ]
    hero_links_html = "".join(
        f'<a class="hero-link{" primary" if index == 0 else ""}" href="{html_escape(href)}">{html_escape(label)}</a>'
        for index, (label, href) in enumerate(hero_links)
    )
    cards = [
        ("Total tickets", compact_number(summary["tickets_total"])),
        ("Open tickets", compact_number(summary["open_tickets"])),
        ("Done / closed", compact_number(summary["done_tickets"])),
        ("Overdue open", compact_number(summary["overdue_open_tickets"])),
        ("Avg comments", compact_number(summary["average_comment_count"])),
        ("Median closure days", compact_number(summary["approx_resolution_days_median"])),
    ]
    cards_html = "".join(
        f'<div class="kpi {tone_class(index)}"><div class="kpi-label">{html_escape(label)}</div><div class="kpi-value">{html_escape(value)}</div></div>'
        for index, (label, value) in enumerate(cards)
    )
    meta_items = (
        [f"Updated: {generated_label}"]
        if root_mode
        else [
            f"Source export: {source_label}",
            f"Analysis output: {output_label}",
            f"Generated: {generated_label}",
        ]
    )
    meta_html = "".join(f"<span>{html_escape(item)}</span>" for item in meta_items)
    visible_insights = (
        [item for item in analysis["insights"] if item.get("title") != "Older tickets note"]
        if root_mode
        else analysis["insights"]
    )
    plots_html = "".join(
        f'<article class="plot-card {"wide" if item["span"] == "wide" else "half"}">'
        f'<div class="plot-meta"><h3>{html_escape(item["title"])}</h3><p>{html_escape(item["subtitle"])}</p></div>'
        f'<img src="{html_escape(plot_base + "/" + item["file"])}" alt="{html_escape(item["title"])}" />'
        f"</article>"
        for item in plot_manifest
    )
    overdue_html = render_rank_list(
        "Overdue tickets to watch",
        analysis["overdue_open_tickets"][:5],
        lambda row: (
            f'<strong>{html_escape(row["title"])}</strong>'
            f'<span>{html_escape(row["lab"])} · {html_escape(row["priority"])} · overdue by {html_escape(compact_number(row["overdue_days"]))} days</span>'
        ),
    )
    comments_html = render_rank_list(
        "Most commented tickets",
        analysis["top_commented_rows"][:5],
        lambda row: (
            f'<strong>{html_escape(row["title"])}</strong>'
            f'<span>{html_escape(row["lab"])} · {html_escape(row["status"])} · {html_escape(compact_number(row["comment_count"]))} '
            f'{"comment" if safe_int(row["comment_count"]) == 1 else "comments"}</span>'
        ),
    )
    root_badge = "Live page snapshot" if root_mode else "Export-specific snapshot"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html_escape(page_title)}</title>
  <link rel="icon" href="assets/favicon.ico" sizes="any">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #07111f;
      --bg-soft: #0f1a2b;
      --panel: rgba(8, 15, 27, 0.78);
      --panel-alt: rgba(250, 250, 252, 0.92);
      --line: rgba(148, 163, 184, 0.18);
      --ink: #edf2f7;
      --muted: #a9b6c8;
      --dark-ink: #0f172a;
      --accent: #4ade80;
      --accent-soft: rgba(74, 222, 128, 0.12);
      --shadow: 0 22px 64px rgba(3, 8, 18, 0.36);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "IBM Plex Sans", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.24), transparent 28%),
        radial-gradient(circle at 85% 15%, rgba(74, 222, 128, 0.14), transparent 28%),
        radial-gradient(circle at bottom right, rgba(244, 114, 182, 0.14), transparent 26%),
        linear-gradient(135deg, #050812, #0c1626 56%, #111d31);
      min-height: 100vh;
    }}
    .page {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 30px 20px 48px;
    }}
    .hero {{
      border: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 30px;
      background:
        radial-gradient(circle at 10% 0%, rgba(96, 165, 250, 0.22), transparent 45%),
        radial-gradient(circle at 85% 12%, rgba(74, 222, 128, 0.18), transparent 42%),
        rgba(8, 15, 27, 0.86);
      box-shadow: var(--shadow);
      padding: 28px;
      margin-bottom: 22px;
      display: grid;
      gap: 18px;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 8px 14px;
      width: fit-content;
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(8, 15, 27, 0.68);
      color: var(--muted);
      font-size: 0.84rem;
    }}
    .eyebrow::before {{
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 16px rgba(74, 222, 128, 0.7);
    }}
    h1, h2, h3 {{
      margin: 0;
      font-family: "Space Grotesk", sans-serif;
      letter-spacing: -0.03em;
    }}
    .hero h1 {{
      font-size: clamp(2.1rem, 4vw, 4.6rem);
      line-height: 0.94;
      max-width: 12ch;
    }}
    .hero p {{
      margin: 0;
      max-width: 70ch;
      color: var(--muted);
      line-height: 1.55;
    }}
    .hero-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .hero-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.2);
      background: rgba(255, 255, 255, 0.04);
      color: var(--ink);
      text-decoration: none;
      font-size: 0.92rem;
      transition: transform 0.08s ease-out, border-color 0.08s ease-out, background 0.08s ease-out;
    }}
    .hero-link:hover {{
      transform: translateY(-1px);
      border-color: rgba(148, 163, 184, 0.42);
      background: rgba(255, 255, 255, 0.08);
    }}
    .hero-link.primary {{
      border-color: rgba(74, 222, 128, 0.42);
      background: rgba(74, 222, 128, 0.12);
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .meta span {{
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.18);
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .kpi {{
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(148, 163, 184, 0.15);
      padding: 16px 18px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
      position: relative;
      overflow: hidden;
      isolation: isolate;
    }}
    .kpi::before,
    .insight::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        radial-gradient(circle at top right, var(--tone-soft, rgba(255, 255, 255, 0.06)), transparent 48%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent 72%);
      z-index: 0;
    }}
    .kpi::after,
    .insight::after {{
      content: "";
      position: absolute;
      inset: 0 auto auto 0;
      width: 100%;
      height: 4px;
      background: linear-gradient(90deg, var(--tone, #4ade80), transparent 80%);
      opacity: 0.9;
      z-index: 1;
    }}
    .kpi > *,
    .insight > * {{
      position: relative;
      z-index: 2;
    }}
    .kpi-label {{
      color: var(--tone, var(--muted));
      font-size: 0.88rem;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .kpi-value {{
      font-family: "Space Grotesk", sans-serif;
      font-size: 2rem;
      font-weight: 700;
    }}
    .insights {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .insight, .plot-card, .list-card {{
      border-radius: 26px;
      border: 1px solid rgba(148, 163, 184, 0.14);
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }}
    .insight {{
      padding: 18px 18px 20px;
      position: relative;
      overflow: hidden;
      isolation: isolate;
    }}
    .insight h3 {{
      color: var(--tone, var(--ink));
    }}
    .insight p {{
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.48;
    }}
    .tone-emerald {{
      --tone: #4ade80;
      --tone-soft: rgba(74, 222, 128, 0.18);
    }}
    .tone-sky {{
      --tone: #38bdf8;
      --tone-soft: rgba(56, 189, 248, 0.18);
    }}
    .tone-violet {{
      --tone: #a78bfa;
      --tone-soft: rgba(167, 139, 250, 0.18);
    }}
    .tone-rose {{
      --tone: #fb7185;
      --tone-soft: rgba(251, 113, 133, 0.18);
    }}
    .tone-amber {{
      --tone: #fbbf24;
      --tone-soft: rgba(251, 191, 36, 0.18);
    }}
    .tone-cyan {{
      --tone: #22d3ee;
      --tone-soft: rgba(34, 211, 238, 0.18);
    }}
    .plot-grid {{
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .plot-card {{
      grid-column: span 6;
      overflow: hidden;
    }}
    .plot-card.half {{
      grid-column: span 6;
    }}
    .plot-card.wide {{
      grid-column: span 12;
    }}
    .plot-meta {{
      padding: 18px 18px 8px;
    }}
    .plot-meta p {{
      margin: 8px 0 0;
      color: var(--muted);
      line-height: 1.45;
      font-size: 0.93rem;
    }}
    .plot-card img {{
      display: block;
      width: 100%;
      background: #ffffff;
      border-top: 1px solid rgba(148, 163, 184, 0.12);
    }}
    .lists {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .list-card {{
      padding: 18px;
    }}
    .list-card ol {{
      margin: 16px 0 0;
      padding-left: 1.2rem;
      display: grid;
      gap: 12px;
    }}
    .list-card li {{
      color: var(--muted);
      line-height: 1.45;
    }}
    .list-card strong {{
      display: block;
      color: var(--ink);
      margin-bottom: 3px;
    }}
    .muted {{
      color: var(--muted);
    }}
    @media (max-width: 1080px) {{
      .plot-card {{
        grid-column: span 6;
      }}
    }}
    @media (max-width: 760px) {{
      .hero {{
        padding: 22px 18px;
      }}
      .plot-card,
      .plot-card.wide,
      .lists {{
        grid-column: span 12;
      }}
      .lists {{
        grid-template-columns: 1fr;
      }}
      .page {{
        padding: 20px 14px 36px;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">{html_escape(root_badge)}</div>
      <h1>{html_escape(page_title)}</h1>
      <p>This page gives a quick view of which labs are sending the most tickets, which categories are filling up, how large requests are, and where work is getting delayed or discussed the most.</p>
      <div class="hero-links">{hero_links_html}</div>
      <div class="meta">{meta_html}</div>
      <section class="kpis">{cards_html}</section>
    </section>

    {render_insights(visible_insights)}

    <section class="plot-grid">{plots_html}</section>

    <section class="lists">
      {overdue_html}
      {comments_html}
    </section>
  </main>
</body>
</html>
"""


def render_report(input_dir: Path, out_dir: Path, analysis: dict[str, Any]) -> str:
    summary = analysis["summary"]
    lines = [
        "# Ticket Analysis Report",
        "",
        f"- Source export: `{input_dir}`",
        f"- Analysis output: `{out_dir}`",
        f"- Generated at: `{format_display_timestamp(summary['generated_at'])}`",
        f"- Site page: `labticketstats.html`",
        "",
        "## Key Takeaways",
        "",
    ]
    lines.extend([f"- **{item['title']}**: {item['detail']}" for item in analysis["insights"]] or ["- No standout findings."])
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Total tickets: **{summary['tickets_total']}**",
            f"- Open tickets: **{summary['open_tickets']}**",
            f"- Done/closed tickets: **{summary['done_tickets']}**",
            f"- Overdue open tickets: **{summary['overdue_open_tickets']}**",
            f"- Average comments per ticket: **{summary['average_comment_count']}**",
            f"- Approx. median resolution days: **{summary['approx_resolution_days_median']}**",
            "",
            "## Labs",
            "",
            render_table(
                analysis["lab_summary_rows"],
                ["lab", "tickets_total", "open_tickets", "done_tickets", "overdue_open_tickets", "median_ticket_age_days"],
            ),
            "",
            "## Categories",
            "",
            render_table(
                analysis["category_summary_rows"],
                ["category", "tickets_total", "open_tickets", "done_tickets", "overdue_open_tickets"],
            ),
            "",
            "## Open Backlog by Priority",
            "",
            render_table(analysis["open_priority_summary_rows"], ["priority", "open_count"]),
            "",
            "## Top Commented Tickets",
            "",
            render_table(analysis["top_commented_rows"][:10], ["ticket_id", "lab", "status", "comment_count", "title"]),
            "",
            "## Overdue Open Tickets",
            "",
            render_table(analysis["overdue_open_tickets"][:10], ["ticket_id", "lab", "priority", "overdue_days", "title"]),
            "",
            "## Notes",
            "",
            "- Closure timing uses `updatedAt` as a proxy because a dedicated close timestamp is not stored yet.",
            "- Requested-vs-final effort and ETA comparisons improve once newer tickets include the original requester snapshot.",
            "",
        ]
    )
    return "\n".join(lines)


def copy_plot_bundle(source_dir: Path, destination_dir: Path) -> None:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    shutil.copytree(source_dir, destination_dir)


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir) if args.input_dir else latest_export_dir(Path("exports"))
    tickets_path = input_dir / "tickets.json"
    comments_path = input_dir / "comments.json"
    if not tickets_path.exists():
        raise FileNotFoundError(f"Missing tickets export: {tickets_path}")

    out_dir = Path(args.out_dir) if args.out_dir else input_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    tickets_raw = load_json(tickets_path)
    if not isinstance(tickets_raw, list):
        raise RuntimeError(f"Unexpected tickets format in {tickets_path}")

    comments_by_ticket = load_comments_by_ticket(comments_path if comments_path.exists() else None)
    analysis = analyze_tickets(
        tickets_raw,
        comments_by_ticket,
        top_keywords=max(1, args.top_keywords),
        keyword_min_count=max(1, args.keyword_min_count),
    )

    plot_manifest = write_plot_svgs(out_dir / "plots", analysis)

    write_json(out_dir / "summary.json", analysis["summary"])
    write_json(out_dir / "insights.json", analysis["insights"])
    write_csv(out_dir / "tickets_enriched.csv", analysis["enriched_rows"])
    write_csv(out_dir / "status_summary.csv", analysis["status_summary_rows"])
    write_csv(out_dir / "priority_summary.csv", analysis["priority_summary_rows"])
    write_csv(out_dir / "open_priority_summary.csv", analysis["open_priority_summary_rows"])
    write_csv(out_dir / "lab_summary.csv", analysis["lab_summary_rows"])
    write_csv(out_dir / "category_summary.csv", analysis["category_summary_rows"])
    write_csv(out_dir / "open_eta_buckets.csv", analysis["open_eta_bucket_rows"])
    write_csv(out_dir / "open_age_buckets.csv", analysis["open_age_bucket_rows"])
    write_csv(out_dir / "top_commented_tickets.csv", analysis["top_commented_rows"])
    write_csv(out_dir / "effort_mismatches.csv", analysis["effort_mismatches"])
    write_csv(out_dir / "eta_changes.csv", analysis["eta_changes"])
    write_csv(out_dir / "overdue_open_tickets.csv", analysis["overdue_open_tickets"])
    write_csv(out_dir / "skill_signals.csv", analysis["skill_signal_rows"])

    export_dashboard = render_stats_html(
        page_title="Lab ticket stats",
        input_dir=input_dir,
        out_dir=out_dir,
        analysis=analysis,
        plot_manifest=plot_manifest,
        plot_base="plots",
        root_mode=False,
    )
    (out_dir / "dashboard.html").write_text(export_dashboard, encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(input_dir, out_dir, analysis), encoding="utf-8")

    ROOT_STATS_ASSETS.mkdir(parents=True, exist_ok=True)
    copy_plot_bundle(out_dir / "plots", ROOT_STATS_ASSETS / "plots")
    write_json(ROOT_STATS_ASSETS / "summary.json", analysis["summary"])
    write_json(ROOT_STATS_ASSETS / "insights.json", analysis["insights"])
    ROOT_STATS_HTML.write_text(
        render_stats_html(
            page_title="Lab Ticket Stats",
            input_dir=input_dir,
            out_dir=out_dir,
            analysis=analysis,
            plot_manifest=plot_manifest,
            plot_base="assets/labticketstats/plots",
            root_mode=True,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "input_dir": str(input_dir),
                "out_dir": str(out_dir),
                "dashboard": str(out_dir / "dashboard.html"),
                "site_page": str(ROOT_STATS_HTML),
                "plots": len(plot_manifest),
                **analysis["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown"
        raise SystemExit(
            "Missing Python dependency "
            f"'{missing}'. Install the script requirements first with:\n"
            f"  {sys.executable} -m pip install -r scripts/requirements.txt"
        ) from exc
