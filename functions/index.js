import { onSchedule } from "firebase-functions/v2/scheduler";
import { initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

initializeApp();
const db = getFirestore();
const BREVO_API_URL = "https://api.brevo.com/v3/smtp/email";

// ENV VARS (set in deploy)
const BREVO_API_KEY = process.env.BREVO_API_KEY;
const FROM_EMAIL = process.env.FROM_EMAIL; // es: "tickets@iit.it"
const SITE_URL = process.env.SITE_URL || ""; // es: "https://riccifab.github.io/xxx"
const CANONICAL_LABS = new Map([
  ["gozzi", "Gozzi"],
  ["iurilli", "Iurilli"],
  ["lombardo", "Lombardo"],
  ["rossi", "Rossi"],
]);

// opzionale: se vuoi escludere anche DONE
const EXCLUDE_DONE = (process.env.EXCLUDE_DONE || "0") === "1";

function normalizeLabKey(value) {
  const parts = String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (parts.at(-1) === "lab") parts.pop();
  return parts.join("");
}

function ticketLab(ticket) {
  const raw = String(ticket.lab || ticket.labKey || "").trim().replace(/\s+/g, " ");
  const key = normalizeLabKey(ticket.labKey || raw);
  return { key: key || "-", label: CANONICAL_LABS.get(key) || raw || "-" };
}

// prende gli admin dalla allowlist
async function getAdminEmails() {
  const snap = await db.collection("allowlist").where("role", "==", "admin").get();
  return snap.docs
    .map(d => d.id) // docId = email (lowercase)
    .filter(Boolean);
}

function fmtTicketLine(t) {
  const pr = t.priority || "-";
  const st = t.status || "-";
  const lab = ticketLab(t).label;
  const exp = t.expectedDeliveryDate || "-";
  const title = (t.title || "").toString().replace(/\s+/g, " ").trim();
  return `- [${pr}] [${st}] [${lab}] exp:${exp} - ${title}`;
}

async function sendBrevoEmail({ to, subject, text }) {
  const recipients = [...new Set(to.map(email => `${email}`.trim()).filter(Boolean))]
    .map(email => ({ email }));

  if (recipients.length === 0) {
    throw new Error("Brevo requires at least one recipient.");
  }

  const response = await fetch(BREVO_API_URL, {
    method: "POST",
    headers: {
      "accept": "application/json",
      "api-key": BREVO_API_KEY,
      "content-type": "application/json"
    },
    body: JSON.stringify({
      sender: { email: FROM_EMAIL },
      to: recipients,
      subject,
      textContent: text
    })
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Brevo error ${response.status}: ${body}`);
  }
}

export const monthlyOpenTicketsDigest = onSchedule(
  {
    schedule: "0 8 1 * *", // 08:00 UTC il giorno 1 di ogni mese
    timeZone: "Europe/Rome",
    region: "europe-west1"
  },
  async () => {
    if (!BREVO_API_KEY || !FROM_EMAIL) {
      console.log("Missing BREVO_API_KEY or FROM_EMAIL");
      return;
    }

    const admins = await getAdminEmails();
    if (admins.length === 0) {
      console.log("No admins found in allowlist");
      return;
    }

    // Query: status != CLOSED (e opzionale != DONE)
    // Nota: Firestore con != richiede orderBy sullo stesso campo.
    let q = db.collection("tickets")
      .where("status", "!=", "CLOSED")
      .orderBy("status")
      .orderBy("updatedAt", "desc")
      .limit(500);

    const snap = await q.get();
    let tickets = snap.docs.map(d => ({ id: d.id, ...d.data() }));

    if (EXCLUDE_DONE) {
      tickets = tickets.filter(t => t.status !== "DONE");
    }

    // summary per lab
    const byLab = new Map();
    for (const t of tickets) {
      const { key, label } = ticketLab(t);
      const current = byLab.get(key) || { label, count: 0 };
      current.count += 1;
      byLab.set(key, current);
    }
    const labsSorted = Array.from(byLab.values()).sort((a, b) => b.count - a.count);

    const header = [];
    header.push("Monthly digest: OPEN tickets");
    header.push(`Total: ${tickets.length}`);
    header.push("");
    header.push("By lab:");
    for (const { label, count } of labsSorted) header.push(`- ${label}: ${count}`);
    header.push("");
    if (SITE_URL) header.push(`Site: ${SITE_URL.replace(/\/$/, "")}/tickets.html`);
    header.push("");

    const lines = tickets.slice(0, 200).map(fmtTicketLine); // evita email infinite
    if (tickets.length > 200) lines.push(`\n(+${tickets.length - 200} more not listed)`);

    const subject = `[Tickets] Monthly OPEN digest - ${tickets.length} open`;

    await sendBrevoEmail({
      to: admins,
      subject,
      text: [...header, ...lines].join("\n")
    });

    console.log(`Sent digest to ${admins.length} admins, tickets=${tickets.length}`);
  }
);
