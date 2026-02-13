import { onSchedule } from "firebase-functions/v2/scheduler";
import { initializeApp } from "firebase-admin/app";
import { getFirestore, FieldPath } from "firebase-admin/firestore";
import sgMail from "@sendgrid/mail";

initializeApp();
const db = getFirestore();

// ENV VARS (set in deploy)
const SENDGRID_API_KEY = process.env.SENDGRID_API_KEY;
const FROM_EMAIL = process.env.FROM_EMAIL; // es: "tickets@iit.it"
const SITE_URL = process.env.SITE_URL || ""; // es: "https://riccifab.github.io/xxx"

// opzionale: se vuoi escludere anche DONE
const EXCLUDE_DONE = (process.env.EXCLUDE_DONE || "0") === "1";

if (SENDGRID_API_KEY) sgMail.setApiKey(SENDGRID_API_KEY);

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
  const lab = t.lab || "-";
  const exp = t.expectedDeliveryDate || "-";
  const title = (t.title || "").toString().replace(/\s+/g, " ").trim();
  return `- [${pr}] [${st}] [${lab}] exp:${exp} — ${title}`;
}

export const monthlyOpenTicketsDigest = onSchedule(
  {
    schedule: "0 8 1 * *", // 08:00 UTC il giorno 1 di ogni mese
    timeZone: "Europe/Rome",
    region: "europe-west1"
  },
  async () => {
    if (!SENDGRID_API_KEY || !FROM_EMAIL) {
      console.log("Missing SENDGRID_API_KEY or FROM_EMAIL");
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
      const lab = (t.lab || "-").toString();
      byLab.set(lab, (byLab.get(lab) || 0) + 1);
    }
    const labsSorted = Array.from(byLab.entries()).sort((a,b) => b[1]-a[1]);

    const header = [];
    header.push(`Monthly digest: OPEN tickets`);
    header.push(`Total: ${tickets.length}`);
    header.push(``);
    header.push(`By lab:`);
    for (const [lab, n] of labsSorted) header.push(`- ${lab}: ${n}`);
    header.push(``);
    if (SITE_URL) header.push(`Site: ${SITE_URL.replace(/\/$/,"")}/tickets.html`);
    header.push(``);

    const lines = tickets.slice(0, 200).map(fmtTicketLine); // evita email infinite
    if (tickets.length > 200) lines.push(`\n(+${tickets.length - 200} more not listed)`);

    const subject = `[Tickets] Monthly OPEN digest — ${tickets.length} open`;

    await sgMail.send({
      to: admins,
      from: FROM_EMAIL,
      subject,
      text: header.join("\n") + lines.join("\n")
    });

    console.log(`Sent digest to ${admins.length} admins, tickets=${tickets.length}`);
  }
);