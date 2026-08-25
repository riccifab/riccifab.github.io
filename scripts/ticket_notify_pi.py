import os
import base64
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from brevo_mail import send_brevo_email
from google.cloud import firestore
from google.oauth2 import service_account
from lab_names import DEFAULT_LABS, canonical_lab_name, normalize_lab_key


# ----------------------------
# Config
# ----------------------------
STATE_PATH = "state/last_run.json"
NOTIFIED_PATH = "state/notified_ticket_ids.json"
LOOKBACK_MINUTES = 3         # evita buchi tra run
MAX_SCAN = 300               # limite sicurezza per run
ALWAYS_CC = ["fabio.ricci@iit.it"]
KNOWN_LAB_KEYS = {normalize_lab_key(lab) for lab in DEFAULT_LABS}


# ----------------------------
# Normalization helpers
# ----------------------------
def norm_lab(x: Any) -> str:
    return normalize_lab_key(x)


def split_emails(x: Any) -> list[str]:
    if not x:
        return []
    return [e.strip() for e in str(x).split(",") if e.strip()]


def parse_created_dt(doc: firestore.DocumentSnapshot, t: dict[str, Any]) -> datetime | None:
    """Best-effort parsing of createdAt from different representations."""

    # Prefer snapshot get() (sometimes safer than to_dict for special types)
    v: Any = None
    try:
        v = doc.get("createdAt")
    except Exception:
        v = t.get("createdAt")

    # Already a python datetime
    if isinstance(v, datetime):
        dt = v
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # Firestore Timestamp-like objects
    if hasattr(v, "to_datetime"):
        try:
            return v.to_datetime().replace(tzinfo=timezone.utc)
        except Exception:
            pass

    # Some SDKs serialize timestamps as dicts
    if isinstance(v, dict):
        # Common shapes: {"seconds":..., "nanoseconds":...} or {"_seconds":..., "_nanoseconds":...}
        sec = v.get("seconds", v.get("_seconds"))
        nsec = v.get("nanoseconds", v.get("_nanoseconds", 0))
        try:
            if sec is not None:
                sec_f = float(sec)
                nsec_f = float(nsec or 0)
                return datetime.fromtimestamp(sec_f + nsec_f * 1e-9, tz=timezone.utc)
        except Exception:
            pass

    # ISO string
    if isinstance(v, str) and v.strip():
        s = v.strip()
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    # epoch seconds / ms
    if isinstance(v, (int, float)):
        try:
            vv = float(v)
            # heuristic: too large => milliseconds
            if vv > 10_000_000_000:
                vv = vv / 1000.0
            return datetime.fromtimestamp(vv, tz=timezone.utc)
        except Exception:
            pass

    # Fallback: Firestore document create_time
    try:
        if hasattr(doc, "create_time") and hasattr(doc.create_time, "to_datetime"):
            return doc.create_time.to_datetime().replace(tzinfo=timezone.utc)
    except Exception:
        pass

    return None


# ----------------------------
# Firestore + state
# ----------------------------
def db_client(project_id: str) -> firestore.Client:
    sa_b64 = os.environ["GCP_SA_KEY_B64"]
    info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=project_id, credentials=creds)


def load_state() -> datetime:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            obj = json.load(f)
        iso = obj.get("last_run_iso", "1970-01-01T00:00:00Z")
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except FileNotFoundError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    except Exception as e:
        # se il file e corrotto non bloccare il workflow
        print(f"WARNING: cannot read state ({STATE_PATH}): {e}")
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def save_state(dt: datetime) -> None:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    obj = {"last_run_iso": dt.isoformat().replace("+00:00", "Z")}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def load_notified_ticket_ids() -> set[str]:
    try:
        with open(NOTIFIED_PATH, "r", encoding="utf-8") as f:
            obj = json.load(f)
        values = obj.get("ticket_ids", []) if isinstance(obj, dict) else []
        return {str(value).strip() for value in values if str(value).strip()}
    except FileNotFoundError:
        return set()
    except Exception as e:
        print(f"WARNING: cannot read state ({NOTIFIED_PATH}): {e}")
        return set()


def save_notified_ticket_ids(ticket_ids: set[str]) -> None:
    os.makedirs(os.path.dirname(NOTIFIED_PATH), exist_ok=True)
    with open(NOTIFIED_PATH, "w", encoding="utf-8") as f:
        json.dump({"ticket_ids": sorted(ticket_ids)}, f)


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


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    project_id = os.environ["FIREBASE_PROJECT_ID"]
    site_url = os.environ.get("SITE_URL", "").rstrip("/")

    brevo_api_key = os.environ["BREVO_API_KEY"]
    mail_from = os.environ["MAIL_FROM"]
    fallback_recipients = split_emails(os.environ.get("LAB_PI_FALLBACK")) or ALWAYS_CC

    lab_pi_map_json = os.environ.get("LAB_PI_MAP", "{}").strip()
    try:
        raw_map = json.loads(lab_pi_map_json) if lab_pi_map_json else {}
        if not isinstance(raw_map, dict):
            raise ValueError("LAB_PI_MAP must be a JSON object (dict).")
    except Exception as e:
        raise RuntimeError(f"Invalid LAB_PI_MAP JSON: {e}")

    # Normalize map keys ONCE (case-insensitive, punctuation-insensitive)
    lab_map: dict[str, str] = {norm_lab(k): str(v).strip() for k, v in raw_map.items()}

    last_run = load_state()
    notified_ticket_ids = load_notified_ticket_ids()
    since = (last_run - timedelta(minutes=LOOKBACK_MINUTES)).astimezone(timezone.utc)

    db = db_client(project_id)

    q = (
        db.collection("tickets")
        .where(filter=firestore.FieldFilter("createdAt", ">=", since))
        .order_by("createdAt", direction=firestore.Query.ASCENDING)
        .limit(MAX_SCAN)
    )

    docs = list(q.stream())
    print(f"DEBUG: last_run={last_run.isoformat()} since={since.isoformat()} docs={len(docs)}")
    print(f"DEBUG: lab_map_keys={sorted(lab_map.keys())}")

    sent = 0
    newest_seen = last_run
    seen_ids: set[str] = set()

    for doc in docs:
        tid = doc.id
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        if tid in notified_ticket_ids:
            continue

        t = doc.to_dict() or {}

        created_dt = parse_created_dt(doc, t)
        if not created_dt:
            print(f"SKIP: ticket={tid} missing/invalid createdAt (keys={sorted(list(t.keys()))})")
            continue
        # Evita doppi invii (LOOKBACK)
        if created_dt <= last_run:
            continue

        # Prendi lab in modo robusto
        lab_disp = canonical_lab_name(t.get("lab") or t.get("labKey"))
        lab_key = norm_lab(t.get("labKey") or lab_disp)

        if not lab_key:
            print(f"SKIP: ticket={tid} lab empty (lab='{lab_disp}' labKey='{t.get('labKey')}')")
            continue

        pi_value = lab_map.get(lab_key)
        if not pi_value and lab_key not in KNOWN_LAB_KEYS:
            pi_value = lab_map.get("other")
        to_list = split_emails(pi_value)
        if not to_list:
            to_list = fallback_recipients
            print(
                f"FALLBACK: ticket={tid} lab='{lab_disp}' labKey='{t.get('labKey')}' "
                f"computed_key='{lab_key}' recipients={to_list}"
            )

        title = " ".join((t.get("title") or "").split())
        pr = t.get("priority", "-")
        st = t.get("status", "NEW")
        exp = t.get("expectedDeliveryDate", "-")
        by = t.get("createdByEmail", "-")

        subject = f"[Ticket] New ({lab_disp or lab_key}) - {title}".strip()
        link = f"{site_url}" if site_url else "(SITE_URL not set)"
        stats_link = build_stats_link(site_url)

        body = "\n".join(
            [
                "A new ticket has been created.",
                "",
                f"Lab: {lab_disp or lab_key}",
                f"Ticket ID: {tid}",
                f"Title: {title}",
                f"Priority: {pr}",
                f"Status: {st}",
                f"Expected: {exp}",
                f"Created by: {by}",
                "",
                f"Tickets page: {link}",
                *([f"Stats page: {stats_link}"] if stats_link else []),
            ]
        )

        send_brevo_email(brevo_api_key, mail_from, to_list, subject, body, cc_list=ALWAYS_CC)
        notified_ticket_ids.add(tid)
        save_notified_ticket_ids(notified_ticket_ids)
        sent += 1
        print(f"SENT: ticket={tid} to={to_list} lab_key={lab_key} createdAt={created_dt.isoformat()}")

        if created_dt > newest_seen:
            newest_seen = created_dt

    # Avanza stato: se non ho trovato nulla, porto avanti comunque a "now"
    now = datetime.now(timezone.utc)
    save_state(max(newest_seen, now))

    print(
        f"OK: scanned={len(docs)} sent={sent} "
        f"since={since.isoformat()} last_run={last_run.isoformat()}"
    )


if __name__ == "__main__":
    main()
