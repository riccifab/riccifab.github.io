#!/usr/bin/env python3
"""Create a ticket for a failed Site checks run on the repository's main branch."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ".github/workflows/site_checks.yml"
FIREBASE_PROJECT = "workstatus-5a293"
FIRESTORE_DATABASE = f"projects/{FIREBASE_PROJECT}/databases/(default)"
REQUEST_TIMEOUT_SECONDS = 20


class TicketError(Exception):
    """A diagnostic that is safe to include in the workflow log."""


class FirebaseRequestError(TicketError):
    """Firebase could have received the request before the connection failed."""


@dataclass(frozen=True)
class FailedRun:
    repository: str
    run_id: int
    commit: str
    expected_delivery_date: str


def as_object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def read_failed_run() -> FailedRun | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_run":
        return None

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
        raise TicketError("Missing or invalid GitHub workflow environment.")

    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TicketError("Could not read the GitHub workflow event.") from None
    if not isinstance(event, dict):
        raise TicketError("Invalid GitHub workflow event.")

    run = as_object(event.get("workflow_run"))
    workflow = as_object(event.get("workflow"))
    event_repository = as_object(event.get("repository"))
    head_repository = as_object(run.get("head_repository"))
    workflow_name = run.get("name") or workflow.get("name")
    workflow_path = run.get("path") or workflow.get("path")
    if (
        event.get("action") != "completed"
        or event_repository.get("full_name") != repository
        or head_repository.get("full_name") != repository
        or workflow_name != "Site checks"
        or not isinstance(workflow_path, str)
        or workflow_path.partition("@")[0] != WORKFLOW_PATH
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("event") != "push"
        or run.get("head_branch") != "main"
    ):
        return None

    run_id = run.get("id")
    commit = run.get("head_sha")
    created_at = run.get("created_at")
    if (
        type(run_id) is not int
        or run_id <= 0
        or not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)
        or not isinstance(created_at, str)
    ):
        raise TicketError("Invalid Site checks run ID, commit, or creation time.")

    try:
        started_at = datetime.fromisoformat(created_at)
        if started_at.tzinfo is None:
            raise ValueError
        due_date = started_at.astimezone(timezone.utc).date() + timedelta(days=7)
    except (ValueError, OverflowError):
        raise TicketError("Invalid Site checks run creation time.") from None

    return FailedRun(repository, run_id, commit.lower(), due_date.isoformat())


def read_firebase_api_key() -> str:
    try:
        frontend = (REPO_ROOT / "tickets.js").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise TicketError("Could not read the public Firebase configuration.") from None

    config = re.search(r"\bconst firebaseConfig\s*=\s*\{([^}]+)\}", frontend)
    if config:
        api_key = re.search(r'\bapiKey:\s*"([A-Za-z0-9_-]+)"', config[1])
        project = re.search(r'\bprojectId:\s*"([a-z0-9-]+)"', config[1])
        if api_key and project and project[1] == FIREBASE_PROJECT:
            return api_key[1]
    raise TicketError("Invalid public Firebase configuration for the ticket project.")


def request_json(
    method: str,
    host: str,
    path: str,
    *,
    operation: str,
    payload: dict | None = None,
    id_token: str | None = None,
) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    request_body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        request_body = json.dumps(payload)
    if id_token is not None:
        headers["Authorization"] = f"Bearer {id_token}"

    connection = http.client.HTTPSConnection(host, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        connection.request(method, path, request_body, headers)
        response = connection.getresponse()
        status = response.status
        raw_body = response.read()
    except (OSError, http.client.HTTPException):
        raise FirebaseRequestError(f"{operation} did not receive a response from Firebase.") from None
    finally:
        connection.close()

    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeError):
        body = {}
    return status, as_object(body)


def sign_in(api_key: str, email: str, password: str) -> tuple[str, str, str]:
    query = urlencode({"key": api_key})
    status, body = request_json(
        "POST",
        "identitytoolkit.googleapis.com",
        f"/v1/accounts:signInWithPassword?{query}",
        operation="Ticket account sign-in",
        payload={"email": email, "password": password, "returnSecureToken": True},
    )
    if status != 200:
        raise TicketError(f"Ticket account sign-in failed (HTTP {status}).")

    token, uid, account_email = (body.get(key) for key in ("idToken", "localId", "email"))
    if (
        not isinstance(token, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", token)
        or not isinstance(uid, str)
        or not uid
        or not isinstance(account_email, str)
        or not account_email
    ):
        raise TicketError("Ticket account sign-in returned an incomplete identity.")

    return token, uid, account_email


def firestore_value(value: object) -> dict:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [firestore_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {key: firestore_value(item) for key, item in value.items()}}}
    raise TicketError("Invalid field in the ticket document.")


def description_prefix(run: FailedRun) -> str:
    return (
        f"The Site checks workflow failed on a push to main in {run.repository}.\n"
        f"Commit: {run.commit}\n"
    )


def ticket_commit(run: FailedRun, document_name: str, uid: str, email: str) -> dict:
    run_url = f"https://github.com/{run.repository}/actions/runs/{run.run_id}"
    requester = {
        "title": f"Site checks failed on main ({run.commit[:12]})",
        "description": f"{description_prefix(run)}Failed workflow run: {run_url}",
        "definitionOfDone": (
            "- Identify and address the failing check.\n"
            "- Verify Site checks passes on main."
        ),
        "lab": "Other",
        "labKey": "other",
        "category": "software",
        "priority": "P2",
        "expectedDeliveryDate": run.expected_delivery_date,
        "hardDeadline": False,
        "hardDeadlineDate": "",
        "commerciallyAvailable": "unknown",
        "commercialLink": "",
        "estimatedCostEUR": None,
        "procurementNeeded": "no",
        "canBeDeferred": "no",
        "deferTo": "",
        "whyNotDeferredCode": "integration",
        "whyNotDeferredText": "Requires a fix in the repository and verification in GitHub Actions.",
        "effortGuess": "M",
        "tags": ["site-checks"],
    }
    ticket = {
        **requester,
        "status": "NEW",
        "requesterSubmission": requester,
        "createdByUid": uid,
        "createdByEmail": email,
        "assigneeEmail": "",
    }
    write = {
        "update": {
            "name": document_name,
            "fields": {key: firestore_value(value) for key, value in ticket.items()},
        },
        "currentDocument": {"exists": False},
        "updateTransforms": [
            {"fieldPath": field, "setToServerValue": "REQUEST_TIME"}
            for field in ("requesterSubmittedAt", "createdAt", "updatedAt")
        ],
    }
    return {"writes": [write]}


def expected_ticket_exists(run: FailedRun, document_name: str, token: str) -> bool:
    query = urlencode({"mask.fieldPaths": "requesterSubmission.description"})
    try:
        status, body = request_json(
            "GET",
            "firestore.googleapis.com",
            f"/v1/{document_name}?{query}",
            operation="Ticket verification",
            id_token=token,
        )
    except FirebaseRequestError:
        return False

    if status != 200 or body.get("name") != document_name:
        return False
    submission = as_object(as_object(body.get("fields")).get("requesterSubmission"))
    fields = as_object(as_object(submission.get("mapValue")).get("fields"))
    description = as_object(fields.get("description")).get("stringValue")
    return isinstance(description, str) and description.startswith(description_prefix(run))


def create_ticket(run: FailedRun, ticket_id: str, token: str, uid: str, email: str) -> bool:
    document_name = f"{FIRESTORE_DATABASE}/documents/tickets/{ticket_id}"
    try:
        status, body = request_json(
            "POST",
            "firestore.googleapis.com",
            f"/v1/{FIRESTORE_DATABASE}/documents:commit",
            operation="Ticket creation",
            payload=ticket_commit(run, document_name, uid, email),
            id_token=token,
        )
    except FirebaseRequestError:
        if expected_ticket_exists(run, document_name, token):
            return False
        raise

    if status == 200:
        return True
    error_status = as_object(body.get("error")).get("status")
    if status == 409 and error_status == "ALREADY_EXISTS":
        return False

    # A retry can fail the precondition or the update rule for an existing document.
    needs_verification = (
        status in (403, 408, 409)
        or status >= 500
        or (status in (400, 412) and error_status == "FAILED_PRECONDITION")
    )
    if needs_verification and expected_ticket_exists(run, document_name, token):
        return False
    raise TicketError(f"Ticket creation failed (HTTP {status}).")


def main() -> int:
    try:
        run = read_failed_run()
        if run is None:
            print("Skipping event outside failed Site checks pushes to main.")
            return 0

        email = os.environ.get("SITE_CHECK_TICKET_EMAIL")
        password = os.environ.get("SITE_CHECK_TICKET_PASSWORD")
        if not email or not password:
            raise TicketError("Missing the Site checks ticket account credentials.")

        token, uid, account_email = sign_in(read_firebase_api_key(), email, password)
        ticket_id = f"{run.commit}-site-checks"
        if create_ticket(run, ticket_id, token, uid, account_email):
            print(f"Created ticket {ticket_id}.")
        else:
            print(f"Ticket {ticket_id} already exists.")
        return 0
    except TicketError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
