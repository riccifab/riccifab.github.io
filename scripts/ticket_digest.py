import os, base64, json
from datetime import datetime, timezone

import requests
from google.cloud import firestore
from google.oauth2 import service_account

PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
EXCLUDE_DONE = (os.environ.get("EXCLUDE_DONE", "0") == "1")

SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
MAIL_FROM = os.environ["MAIL_FROM"]

def db_client():
    sa_b64 = os.environ["GCP_SA_KEY_B64"]
    info = json.loads(base64.b64decode(sa_b64).decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=PROJECT_ID, credentials=creds)

def get_admin_emails(db):
    admins = []
    for d in db.collection("allowlist").where("role", "==", "admin").stream():
        admins.append(d.id)  # docId = email
    admins = sorted(set(x for x in admins if x))
    if not admins:
        raise RuntimeError("No admins found: allowlist where role==admin returned empty.")
    return admins

def get_open_tickets(db, limit_n=500):
    q = (db.collection("tickets")
         .where("status", "!=", "CLOSED")
         .order_by("status")
         .order_by("updatedAt", direction=firestore.Query.DESCENDING)
         .limit(limit_n))
    tickets = []
    for doc in q.stream():
        t = doc.to_dict()
        t["id"] = doc.id
        tickets.append(t)
    if EXCLUDE_DONE:
        tickets = [t for t in tickets if t.get("status") != "DONE"]
    return tickets

def by_lab_counts(tickets):
    m = {}
    for t in tickets:
        lab = (t.get("labKey") or t.get("lab") or "-").strip().lower()
        m[lab] = m.get(lab, 0) + 1
    return sorted(m.items(), key=lambda kv: kv[1], reverse=True)

def fmt_ticket(t):
    pr = t.get("priority", "-")
    st = t.get("status", "-")
    lab = (t.get("lab") or "-").strip()
    exp = t.get("expectedDeliveryDate", "-")
    title = " ".join((t.get("title") or "").split())
    tid = (t.get("id") or "")[:8]
    return f"- [{pr}] [{st}] [{lab}] exp:{exp} #{tid} — {title}"

def build_body(tickets):
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    labs = by_lab_counts(tickets)

    lines = []
    lines.append("Weekly digest: OPEN tickets")
    lines.append(f"Generated: {now}")
    lines.append(f"Total open: {len(tickets)}")
    lines.append("")
    lines.append("By lab:")
    for lab, n in labs:
        lines.append(f"- {lab}: {n}")
    lines.append("")

    if SITE_URL:
        lines.append(f"Site: {SITE_URL}")
        lines.append("")

    lines.append("Tickets (first 200):")
    for t in tickets[:200]:
        lines.append(fmt_ticket(t))

    if len(tickets) > 200:
        lines.append("")
        lines.append(f"(+{len(tickets)-200} more not listed)")

    return "\n".join(lines)

def send_sendgrid(to_list, subject, body):
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "personalizations": [{"to": [{"email": e} for e in to_list]}],
        "from": {"email": MAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
        "tracking_settings": {
            "click_tracking": { "enable": False, "enable_text": False }
        }
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")

def main():
    db = db_client()
    admins = get_admin_emails(db)
    tickets = get_open_tickets(db)
    subject = f"[Tickets] Weekly OPEN digest — {len(tickets)} open"
    body = build_body(tickets)
    send_sendgrid(admins, subject, body)
    print(f"OK: sent to {len(admins)} admins, tickets={len(tickets)}")

if __name__ == "__main__":
    main()