import os, base64, json
from datetime import datetime, timezone, timedelta

import requests
from google.cloud import firestore
from google.oauth2 import service_account

PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
MAIL_FROM = os.environ["MAIL_FROM"]
LAB_PI_MAP_JSON = os.environ.get("LAB_PI_MAP", "{}")

STATE_PATH = "state/last_run.json"
LOOKBACK_MINUTES = 3   # per evitare buchi tra run
ALWAYS_CC = ["fabio.ricci@iit.it"]
def norm(s: str) -> str:
    return " ".join((s or "").split()).strip().lower()

def db_client():
    sa_b64 = os.environ["GCP_SA_KEY_B64"]
    info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=PROJECT_ID, credentials=creds)

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

def save_state(dt: datetime):
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    obj = {"last_run_iso": dt.isoformat().replace("+00:00", "Z")}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f)

def send_sendgrid(to_list, subject, body):
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }

    personalization = {"to": [{"email": e} for e in to_list]}
    if ALWAYS_CC:
        personalization["cc"] = [{"email": e} for e in ALWAYS_CC]

    payload = {
        "personalizations": [personalization],
        "from": {"email": MAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
        "tracking_settings": {
            "click_tracking": {"enable": False, "enable_text": False}
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")

def main():
    raw = json.loads(LAB_PI_MAP_JSON) if LAB_PI_MAP_JSON.strip() else {}
    lab_map = {norm(k): v for k, v in raw.items()}

    last_run = load_state()
    since = (last_run - timedelta(minutes=LOOKBACK_MINUTES)).astimezone(timezone.utc)

    db = db_client()

    q = (
        db.collection("tickets")
        .where("createdAt", ">=", since)
        .order_by("createdAt", direction=firestore.Query.ASCENDING)
        .limit(300)
    )

    docs = list(q.stream())
    sent = 0
    newest = last_run
    seen = set()

    for doc in docs:
        tid = doc.id
        if tid in seen:
            continue
        seen.add(tid)

        t = doc.to_dict() or {}
        created_at = t.get("createdAt")
        if not hasattr(created_at, "to_datetime"):
            continue
        created_dt = created_at.to_datetime().replace(tzinfo=timezone.utc)

        # evita doppi invii (lookback)
        if created_dt <= last_run:
            continue

        lab_disp = (t.get("lab") or "").strip()
        lab_key = norm(t.get("labKey") or lab_disp)
        pi_value = lab_map.get(lab_key)

        if not pi_value:
            # nessun PI configurato per questo lab -> skip
            continue

        # supporta più destinatari separati da virgola
        to_list = [x.strip() for x in str(pi_value).split(",") if x.strip()]
        if not to_list:
            continue

        title = " ".join((t.get("title") or "").split())
        pr = t.get("priority", "-")
        st = t.get("status", "NEW")
        exp = t.get("expectedDeliveryDate", "-")
        by = t.get("createdByEmail", "-")

        subject = f"[Ticket] New ({lab_disp or lab_key}) — {title}"
        link = f"{SITE_URL}/tickets.html" if SITE_URL else "(SITE_URL not set)"

        body = "\n".join([
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
        ])

        send_sendgrid(to_list, subject, body)
        sent += 1

        if created_dt > newest:
            newest = created_dt

    # avanza lo stato (se non ho inviato nulla, avanzalo comunque a now per non ristare “indietro”)
    now = datetime.now(timezone.utc)
    save_state(max(newest, now))

    print(f"OK: scanned={len(docs)} sent={sent} since={since.isoformat()} last_run={last_run.isoformat()}")

if __name__ == "__main__":
    main()