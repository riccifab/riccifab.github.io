# Workstatus Ticketing Website

[![Notify PI](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml)
[![Weekly digest](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml)

![Hero screenshot placeholder](docs/img/hero.png)

A lightweight, GitHub Pages–hosted ticketing website used to track lab/tech requests and keep stakeholders in the loop via automated email notifications.

> **Images:** the `docs/img/*.png` paths are placeholders. Drop your screenshots there (or change paths) and the README will render them.

---

## What this is

- **Frontend:** static website served from GitHub Pages.
- **Backend:** Google **Firestore** used as the ticket database.
- **Automation:** GitHub Actions + Python scripts send emails (SendGrid) and keep bot state in a dedicated branch.

![Architecture diagram placeholder](docs/img/architecture.png)

---

## Features

- Create and manage tickets (status, due date, notes, etc.)
- Automated notifications:
  - **New ticket → PI notification** (based on lab key)
  - **Ticket updated → requester notification** (diff-style “what changed”)
  - **Weekly digest** (summary email)
- Bot state persisted in a separate branch (`bot-state`) so scheduled workflows are deterministic

![Ticket list placeholder](docs/img/ticket_list.png)

---

## Repositories

- **This website + automations:** `riccifab/riccifab.github.io`
- **Universal headframe (related hardware repo):** `iurillilab/Universal_Headframe`

![Related project placeholder](docs/img/related_project.png)

---

## GitHub Actions Workflows

Badges (workflows):

- Notify PI on new tickets: `ticket_notify_pi.yml`
- Weekly ticket digest: `weekly_ticket_digest.yml`

Workflows typically:

1. Checkout `main`
2. Checkout `bot-state` into `bot-state/`
3. Copy `bot-state/state/*.json` into a working `state/` folder
4. Run a Python script in `scripts/`
5. Update `bot-state/state/*.json`, commit and push back to `bot-state`

---

## Bot state branch (`bot-state`)

The `bot-state` branch stores small JSON files used by scheduled jobs (e.g. last run timestamps) so the bot knows what it already processed.

Expected layout in `bot-state`:

```
state/
  last_run.json
  last_update_run.json
  ...
```

> If you add a new scheduled script, add a dedicated state file so the job can be idempotent.

---

## Configuration

The automations read configuration from **GitHub Actions variables and secrets**.

### Non-secret variables (GitHub Actions “Variables”)

- `FIREBASE_PROJECT_ID` — Firestore project id
- `SITE_URL` — GitHub Pages URL (e.g. `https://riccifab.github.io/`)
- `LAB_PI_MAP` — JSON map from `lab_key` → PI email
  - Example:
    ```json
    {
      "gozzi": "alessandro.gozzi@iit.it",
      "rossi": "federico.rossi@iit.it"
    }
    ```
- `MAIL_CC` — optional CC recipients (e.g. `fabio.ricci@iit.it`)

### Secrets (GitHub Actions “Secrets”)

- `GCP_SA_KEY_B64` — base64-encoded GCP service account JSON with Firestore access
- `SENDGRID_API_KEY` — SendGrid API key
- `MAIL_FROM` — verified sender address

![Secrets UI placeholder](docs/img/secrets.png)

---

## Local development (scripts)

If you want to run the Python scripts locally:

1. Create a virtualenv
2. Install requirements from `scripts/requirements.txt`
3. Export the same env vars you use in Actions (or load them via `.env`)

**Note:** local runs should point to the same Firestore project, so be careful not to spam real users.

---

## Email content rules

- **Requester updates:** must show a clear list of changed fields (e.g. due date changes).
- **Links:** emails should use `SITE_URL` (direct site URL), not SendGrid click-tracking links.
- **PI notifications:** only triggered on ticket creation, routed by `lab_key`.

---

## Screenshots to add

Drop screenshots here (or update paths):

- `docs/img/hero.png`
- `docs/img/architecture.png`
- `docs/img/ticket_list.png`
- `docs/img/related_project.png`
- `docs/img/secrets.png`

---

## License

This project is released under the **MIT License**.

- You can use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the software.
- The software is provided **“as is”**, without warranty of any kind.

> Add the full license text in a `LICENSE` file at the repository root (standard MIT template).
  
---

## Maintainers

- Fabio Ricci
