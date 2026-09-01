# Workstatus Ticketing Website

[![Notify PI](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml)
[![Weekly digest](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml)
[![ticket-update-notify](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_update.yml/badge.svg)](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_update.yml)
[![Daily ticket analysis](https://github.com/riccifab/riccifab.github.io/actions/workflows/daily_ticket_analysis.yml/badge.svg)](https://github.com/riccifab/riccifab.github.io/actions/workflows/daily_ticket_analysis.yml)
[![Sync work_status in-progress tickets](https://github.com/riccifab/riccifab.github.io/actions/workflows/work_status_sync.yml/badge.svg)](https://github.com/riccifab/riccifab.github.io/actions/workflows/work_status_sync.yml)

<img width="840" height="374" alt="Screenshot 2026-02-13 alle 21 18 32" src="https://github.com/user-attachments/assets/6c868082-30ce-461f-80d8-9414cbebbff9" />

A lightweight, GitHub Pages-hosted ticketing website used to track lab/tech requests and keep stakeholders in the loop via automated email notifications.

---

## What this is

- **Frontend:** static website served from GitHub Pages.
- **Backend:** Google **Firestore** used as the ticket database.
- **Automation:** GitHub Actions + Python scripts send emails via Brevo and keep bot state in a dedicated branch.

---

## Features

- Create and manage tickets (status, due date, notes, etc.)
- Role-based access for admins, PIs, technicians, postdocs, and PhD users
- Automated notifications:
  - **New ticket -> PI notification** (based on lab key)
  - **Ticket updated -> requester notification** (diff-style "what changed")
  - **Weekly digest** (summary email)
- Bot state persisted in a separate branch (`bot-state`) so scheduled workflows are deterministic
<img width="464" height="647" alt="Screenshot 2026-02-13 alle 21 23 18" src="https://github.com/user-attachments/assets/fdb06d8c-114f-4c58-9f6a-f439913aa9e9" />

---

## Email setup

Configure these secrets where email jobs run:

- `BREVO_API_KEY`
- `MAIL_FROM`

Configure `LAB_PI_MAP` as a repository variable containing a JSON object whose
keys are lab names and whose values are PI email addresses. Matching ignores
case, spacing, punctuation, and an optional trailing `Lab`. `other` can be used
as the recipient for custom labs; `LAB_PI_FALLBACK` is the optional final
fallback and defaults to the maintainer notification address.

The Firebase scheduled function in `functions/index.js` also expects `BREVO_API_KEY` in its deployed environment.

---

## Firestore access rules

Firestore rules are versioned in `firestore.rules`. Supported allowlist roles are
`admin`, `pi`, `technician`, `postdoc`, and `phd`; the legacy plural value
`technicians` is accepted and normalized by the frontend.

Technicians can read all tickets, create tickets for any lab, update the fields
exposed by the ticket controls, and add comments. They cannot manage the
allowlist/users or delete tickets/comments. Postdocs and PhD users can create
`Other` tickets and retain access to tickets they created.

Deploy only the Firestore rules with:

```bash
firebase deploy --only firestore:rules --project workstatus-5a293
```

---

## Local export and analysis

For local data export and reporting, the simplest path is to drop your Google service-account JSON into `local/credentials/service-account.json`.

1. Put your JSON key here:

```bash
cp /path/to/your/service-account.json local/credentials/service-account.json
```

2. Prepare the local Python environment:

```bash
./scripts/setup_python_env.sh
```

3. Export tickets and generate the analysis report:

```bash
./scripts/run_ticket_export_analysis.sh
```

Outputs are written under `exports/tickets-*/` and `exports/tickets-*/analysis/`, including a Markdown report, a visual `dashboard.html`, CSV summaries, and SVG plots.

If you prefer storing the JSON elsewhere on your machine, create `.env.local` from `.env.local.example` and set `GOOGLE_APPLICATION_CREDENTIALS` there instead.

---

## License

This project is released under the **MIT License**.

- You can use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the software.
- The software is provided **"as is"**, without warranty of any kind.

---

## Maintainers

- Fabio Ricci
