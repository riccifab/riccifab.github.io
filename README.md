# Workstatus Ticketing Website

[![Notify PI](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_notify_pi.yml)
[![Weekly digest](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml/badge.svg?branch=main)](https://github.com/riccifab/riccifab.github.io/actions/workflows/weekly_ticket_digest.yml)
[![ticket-update-notify](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_update.yml/badge.svg)](https://github.com/riccifab/riccifab.github.io/actions/workflows/ticket_update.yml)

<img width="840" height="374" alt="Screenshot 2026-02-13 alle 21 18 32" src="https://github.com/user-attachments/assets/6c868082-30ce-461f-80d8-9414cbebbff9" />

A lightweight, GitHub Pages–hosted ticketing website used to track lab/tech requests and keep stakeholders in the loop via automated email notifications.

---

## What this is

- **Frontend:** static website served from GitHub Pages.
- **Backend:** Google **Firestore** used as the ticket database.
- **Automation:** GitHub Actions + Python scripts send emails (SendGrid) and keep bot state in a dedicated branch.

---

## Features

- Create and manage tickets (status, due date, notes, etc.)
- Automated notifications:
  - **New ticket → PI notification** (based on lab key)
  - **Ticket updated → requester notification** (diff-style “what changed”)
  - **Weekly digest** (summary email)
- Bot state persisted in a separate branch (`bot-state`) so scheduled workflows are deterministic
<img width="464" height="647" alt="Screenshot 2026-02-13 alle 21 23 18" src="https://github.com/user-attachments/assets/fdb06d8c-114f-4c58-9f6a-f439913aa9e9" />

---


## License

This project is released under the **MIT License**.

- You can use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the software.
- The software is provided **“as is”**, without warranty of any kind.

---

## Maintainers

- Fabio Ricci
