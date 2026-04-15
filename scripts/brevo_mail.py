from __future__ import annotations

from typing import Iterable

import requests


BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _recipient_objects(emails: Iterable[str]) -> list[dict[str, str]]:
    recipients: list[dict[str, str]] = []
    seen: set[str] = set()

    for email in emails:
        value = str(email).strip()
        if not value:
            continue

        lowered = value.lower()
        if lowered in seen:
            continue

        seen.add(lowered)
        recipients.append({"email": value})

    return recipients


def send_brevo_email(
    api_key: str,
    mail_from: str,
    to_list: Iterable[str],
    subject: str,
    body: str,
    cc_list: Iterable[str] | None = None,
) -> None:
    to_recipients = _recipient_objects(to_list)
    if not to_recipients:
        raise ValueError("Brevo requires at least one recipient.")

    cc_recipients = _recipient_objects(cc_list or [])
    to_lower = {item["email"].lower() for item in to_recipients}
    cc_recipients = [item for item in cc_recipients if item["email"].lower() not in to_lower]

    payload = {
        "sender": {"email": mail_from},
        "to": to_recipients,
        "subject": subject,
        "textContent": body,
    }
    if cc_recipients:
        payload["cc"] = cc_recipients

    response = requests.post(
        BREVO_API_URL,
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Brevo error {response.status_code}: {response.text}")
