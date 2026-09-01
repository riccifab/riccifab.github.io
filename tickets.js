import {
  CANONICAL_LABS,
  LAB_OPTIONS,
  OTHER_LAB_NAME,
  canonicalizeLabName,
  legacyLabAliases,
  normalizeLabKey,
} from "./lab-config.mjs?v=20260825b";

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  sendPasswordResetEmail,
  signOut,
  onAuthStateChanged
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

import {
  getFirestore,
  doc,
  getDoc,
  collection,
  addDoc,
  getDocs,
  query,
  where,
  orderBy,
  limit,
  increment,
  updateDoc,
  serverTimestamp
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";

/* ========= Firebase config (PASTE YOURS) ========= */

const firebaseConfig = {
  apiKey: "AIzaSyCymGUKXdpBtVJPC1YH2bLuRLc16p-E93A",
  authDomain: "workstatus-5a293.firebaseapp.com",
  databaseURL: "https://workstatus-5a293-default-rtdb.europe-west1.firebasedatabase.app",
  projectId: "workstatus-5a293",
  storageBucket: "workstatus-5a293.firebasestorage.app",
  messagingSenderId: "737892892698",
  appId: "1:737892892698:web:6f7112f9f5e724625451a3",
  measurementId: "G-60KPLB1GDG"
};
  
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

const $ = (id) => document.getElementById(id);

/* ========= UI ========= */
const pillDot = $("pillDot");
const pillText = $("pillText");

const authCard = $("authCard");
const authMsg = $("authMsg");

const loginForm = $("loginForm");
const loginEmail = $("loginEmail");
const loginPass = $("loginPass");
const btnSignIn = $("btnSignIn");
const btnReset = $("btnReset");
const btnSignOut = $("btnSignOut");

const appSection = $("app");
const roleInfo = $("roleInfo");

const btnNewTicket = $("btnNewTicket");
const btnRefresh = $("btnRefresh");

const fSearch = $("fSearch");
const fStatus = $("fStatus");
const fPriority = $("fPriority");
const fLab = $("fLab");
const fCategory = $("fCategory");

const ticketsTbody = $("ticketsTbody");
const ticketsCount = $("ticketsCount");

const selectedIdPill = $("selectedIdPill");
const ticketDetails = $("ticketDetails");

const commentsBox = $("commentsBox");
const commentsInfo = $("commentsInfo");
const commentInput = $("commentInput");
const btnAddComment = $("btnAddComment");
const commentMsg = $("commentMsg");

const modal = $("modal");
const btnCloseModal = $("btnCloseModal");
const ticketForm = $("ticketForm");
const formMsg = $("formMsg");
const tLab = $("tLab");

/* ========= State ========= */
let currentUser = null;
let currentRole = null; // "admin" | "pi" | "technician" | "postdoc" | "phd"
let currentLab = null;
let currentLabKey = null;

let ticketsCache = [];
let selectedTicketId = null;
let refreshNewTicketFormState = () => {};
let modalReturnFocus = null;

const STATUS = ["NEW","TRIAGE","APPROVED","REJECTED","IN_PROGRESS","WAITING_ON_PI","WAITING_ON_PROCUREMENT","BLOCKED","DONE","CLOSED"];
const PRIORITY = ["P0","P1","P2","P3"];
const CATEGORIES = ["electronics", "mechanics", "optics", "software", "procurement", "other"];
const YES_NO_UNKNOWN = ["unknown", "yes", "no"];

/* ========= Helpers ========= */
function setPill(signedIn, text) {
  pillText.textContent = text;
  if (signedIn) {
    pillDot.style.background = "#22c55e";
    pillDot.style.boxShadow = "0 0 10px rgba(34,197,94,0.8)";
  } else {
    pillDot.style.background = "#64748b";
    pillDot.style.boxShadow = "0 0 10px rgba(100,116,139,0.35)";
  }
}
function setMsg(el, text, type = "") {
  el.textContent = text || "";
  el.classList.remove("ok", "err");
  if (type) el.classList.add(type);
}
function safeText(value) {
  const escapes = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (character) => escapes[character]);
}
function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
function resolvedLabSelection() {
  const lab = canonicalizeLabName(tLab.value);
  return { lab, labKey: normalizeLabKey(lab) };
}
function setLabSelectorValue(value) {
  const canonical = canonicalizeLabName(value);
  if (!canonical) {
    tLab.value = "";
    return;
  }

  tLab.value = LAB_OPTIONS.includes(canonical) ? canonical : OTHER_LAB_NAME;
}
function populateLabSelector() {
  const placeholder = new Option("Select a lab", "", true, true);
  placeholder.disabled = true;
  tLab.replaceChildren(
    placeholder,
    ...LAB_OPTIONS.map((lab) => new Option(lab, lab)),
  );
}
populateLabSelector();
function optionList(options, selectedValue, { allowCustom = false, emptyLabel = null } = {}) {
  const rendered = [];

  if (emptyLabel !== null) {
    rendered.push(`<option value="" ${!selectedValue ? "selected" : ""}>${safeText(emptyLabel)}</option>`);
  }

  options.forEach((value) => {
    rendered.push(`<option value="${safeText(value)}" ${selectedValue === value ? "selected" : ""}>${safeText(value)}</option>`);
  });

  if (allowCustom && selectedValue && !options.includes(selectedValue)) {
    rendered.push(`<option value="${safeText(selectedValue)}" selected>${safeText(selectedValue)}</option>`);
  }

  return rendered.join("");
}
function fmtDateTime(ts) {
  if (!ts) return "-";
  try {
    if (typeof ts.toDate === "function") {
      const d = ts.toDate();
      return d.toISOString().replace("T"," ").slice(0,16);
    }
  } catch {}
  return "-";
}

function bindAutoDatePickers(root = document) {
  const nodes = root.querySelectorAll?.('input[type="date"]') || [];
  nodes.forEach((input) => {
    if (input.dataset.autoPickerBound === "1") return;
    input.dataset.autoPickerBound = "1";
    input.addEventListener("click", () => {
      if (input.disabled || input.readOnly || typeof input.showPicker !== "function") return;
      try { input.showPicker(); } catch {}
    });
  });
}

function buildRequesterSubmissionSnapshot(data) {
  return {
    title: data.title || "",
    description: data.description || "",
    definitionOfDone: data.definitionOfDone || "",
    lab: data.lab || "",
    labKey: data.labKey || normalizeLabKey(data.lab),
    category: data.category || "",
    priority: data.priority || "",
    expectedDeliveryDate: data.expectedDeliveryDate || "",
    hardDeadline: !!data.hardDeadline,
    hardDeadlineDate: data.hardDeadlineDate || "",
    commerciallyAvailable: data.commerciallyAvailable || "unknown",
    commercialLink: data.commercialLink || "",
    estimatedCostEUR: data.estimatedCostEUR ?? null,
    procurementNeeded: data.procurementNeeded || "unknown",
    canBeDeferred: data.canBeDeferred || "no",
    deferTo: data.deferTo || "",
    whyNotDeferredCode: data.whyNotDeferredCode || "",
    whyNotDeferredText: data.whyNotDeferredText || "",
    effortGuess: data.effortGuess || "",
    tags: Array.isArray(data.tags) ? data.tags : []
  };
}

function requesterSnapshotHtml(t) {
  const r = t?.requesterSubmission;
  if (!r || typeof r !== "object") return "";

  return `
    <hr class="sep"/>

    <div class="muted small">Requester original input</div>
    <div class="grid2" style="margin-top:0.6rem;">
      <div><div class="muted small">Requested priority</div><div class="code">${safeText(r.priority || "-")}</div></div>
      <div><div class="muted small">Requested ETA</div><div class="code">${safeText(r.expectedDeliveryDate || "-")}</div></div>
      <div><div class="muted small">Requested lab / category</div><div class="code">${safeText(canonicalizeLabName(r.lab || r.labKey) || "-")} • ${safeText(r.category || "-")}</div></div>
      <div><div class="muted small">Requested effort</div><div class="code">${safeText(r.effortGuess || "-")}</div></div>
    </div>
  `;
}

function syncConditionalRequiredField(input, active) {
  if (!input) return;
  input.required = active;
  const label = input.closest("label");
  if (label) label.classList.toggle("required-field", active || label.querySelector("[required]") !== null);
}

function setModalBackgroundInert(active) {
  Array.from(modal.parentElement?.children || [])
    .filter((element) => element !== modal)
    .forEach((element) => { element.inert = active; });
}

function openModal() {
  modalReturnFocus = document.activeElement;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  setModalBackgroundInert(true);
  setMsg(formMsg, "");
  setLabSelectorValue(currentLab);
  refreshNewTicketFormState();
  requestAnimationFrame(() => tLab.focus());
}
function closeModal() {
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  setModalBackgroundInert(false);
  ticketForm.reset();
  setMsg(formMsg, "");
  refreshNewTicketFormState();
  if (modalReturnFocus instanceof HTMLElement) modalReturnFocus.focus();
  modalReturnFocus = null;
}

/* ========= Modal wiring ========= */
btnCloseModal.addEventListener("click", closeModal);
btnNewTicket.addEventListener("click", openModal);
modal.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModal();
});
bindAutoDatePickers(document);

/* ========= Allowlist ========= */
async function ensureAllowedOrThrow(user) {
  const rawEmail = user?.email;
  if (!rawEmail) throw new Error("No email in auth user.");

  const email = rawEmail.toString().trim().toLowerCase();

  const ref = doc(db, "allowlist", email);
  const snap = await getDoc(ref);
  if (!snap.exists()) throw new Error(`Email not in allowlist: ${email}`);

  const data = snap.data();
  const rawRole = (data.role || "phd").toString().trim().toLowerCase();
  const role = rawRole === "technicians" ? "technician" : rawRole;
  const allowedRoles = ["admin","pi","technician","postdoc","phd"];
  if (!allowedRoles.includes(role)) throw new Error(`Invalid role in allowlist: "${role}"`);

  currentRole = role;
  currentLab = (data.lab || "").toString().trim().replace(/\s+/g, " ");
  currentLabKey = normalizeLabKey(data.labKey || currentLab);
  return { role, lab: canonicalizeLabName(currentLab), labKey: currentLabKey };
}

/* ========= Auth ========= */
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (btnSignIn.disabled) return;

  setMsg(authMsg, "Signing in...");
  btnSignIn.disabled = true;
  btnSignIn.setAttribute("aria-busy", "true");
  btnSignIn.textContent = "Signing in...";

  try {
    const email = (loginEmail.value || "").trim();
    const pass = loginPass.value || "";
    if (!email || !pass) {
      setMsg(authMsg, "Insert email + password.", "err");
      return;
    }
    await signInWithEmailAndPassword(auth, email, pass);
  } catch (e) {
    setMsg(authMsg, `Login error: ${e?.message || e}`, "err");
  } finally {
    btnSignIn.disabled = false;
    btnSignIn.removeAttribute("aria-busy");
    btnSignIn.textContent = "Sign in";
  }
});

btnReset.onclick = async () => {
  try {
    const email = (loginEmail.value || "").trim();
    if (!email) {
      setMsg(authMsg, "Insert email for reset.", "err");
      return;
    }
    await sendPasswordResetEmail(auth, email);
    setMsg(authMsg, "Reset email sent (check inbox/spam).", "ok");
  } catch (e) {
    setMsg(authMsg, `Reset error: ${e?.message || e}`, "err");
  }
};

btnSignOut.onclick = async () => { await signOut(auth); };

onAuthStateChanged(auth, async (user) => {
  currentUser = user;

  // reset UI
  selectedTicketId = null;
  ticketsCache = [];
  renderTicketsTable([]);
  renderDetails(null);
  renderComments(null);

  if (!user) {
    authCard.classList.remove("hidden");
    appSection.classList.add("hidden");
    btnSignOut.classList.add("hidden");
    btnSignIn.classList.remove("hidden");
    btnReset.classList.remove("hidden");
    setPill(false, "Signed out");
    setMsg(authMsg, "");
    return;
  }

  setMsg(authMsg, "Checking allowlist...");
  try {
    const { role, lab } = await ensureAllowedOrThrow(user);

    authCard.classList.add("hidden");
    appSection.classList.remove("hidden");

    btnSignOut.classList.remove("hidden");
    btnSignIn.classList.add("hidden");
    btnReset.classList.add("hidden");

    setPill(true, `${user.email} • ${role}`);
    roleInfo.textContent = `Role: ${role}${lab ? " • Lab: " + lab : ""}`;

    await refreshTickets();
  } catch (e) {
    setMsg(authMsg, `Access denied: ${e?.message || e}`, "err");
    await signOut(auth);
  }
});

/* ========= Tickets list ========= */
btnRefresh.onclick = refreshTickets;
[fSearch, fStatus, fPriority, fLab, fCategory].forEach(el => {
  el.addEventListener("input", applyFiltersAndRender);
  el.addEventListener("change", applyFiltersAndRender);
});

async function refreshTickets() {
  if (!currentUser) return;

  setMsg(ticketsCount, "Loading...");
  selectedTicketId = null;
  renderDetails(null);
  renderComments(null);

  const tcol = collection(db, "tickets");
  let ticketDocs;

  if (["admin", "pi", "technician"].includes(currentRole)) {
    const snap = await getDocs(query(tcol, orderBy("updatedAt", "desc"), limit(300)));
    ticketDocs = snap.docs;
  } else if (currentRole === "postdoc" || currentRole === "phd") {
    ticketDocs = await loadScopedTicketDocs(tcol);
  } else {
    const snap = await getDocs(query(tcol, where("createdByUid", "==", currentUser.uid), limit(300)));
    ticketDocs = snap.docs;
  }

  ticketsCache = ticketDocs.map(d => ({ id: d.id, ...d.data() }));

  // client sort for PI (and safe fallback)
  ticketsCache.sort((a,b) => {
    const ta = a.updatedAt?.toMillis?.() ?? a.createdAt?.toMillis?.() ?? 0;
    const tb = b.updatedAt?.toMillis?.() ?? b.createdAt?.toMillis?.() ?? 0;
    return tb - ta;
  });

  const previousLabFilter = fLab.value;
  const labsByKey = new Map();
  ticketsCache.forEach((ticket) => {
    const label = canonicalizeLabName(ticket.lab || ticket.labKey);
    const key = normalizeLabKey(ticket.labKey || label);
    if (key && !labsByKey.has(key)) labsByKey.set(key, label);
  });

  const canonicalOrder = new Map(LAB_OPTIONS.map((lab, index) => [normalizeLabKey(lab), index]));
  const labOptions = Array.from(labsByKey, ([key, label]) => ({ key, label })).sort((a, b) => {
    const aOrder = canonicalOrder.get(a.key) ?? Number.MAX_SAFE_INTEGER;
    const bOrder = canonicalOrder.get(b.key) ?? Number.MAX_SAFE_INTEGER;
    return aOrder - bOrder || a.label.localeCompare(b.label, undefined, { sensitivity: "base" });
  });

  fLab.replaceChildren(
    new Option("(all)", ""),
    ...labOptions.map(({ key, label }) => new Option(label, key)),
  );
  if (labOptions.some(({ key }) => key === previousLabFilter)) fLab.value = previousLabFilter;

  applyFiltersAndRender();
}

async function loadScopedTicketDocs(tcol) {
  const scopes = [];
  const seenScopes = new Set();
  const addScope = (field, operator, value) => {
    if (!value || (Array.isArray(value) && value.length === 0)) return;
    const scopeKey = `${field}:${operator}:${JSON.stringify(value)}`;
    if (seenScopes.has(scopeKey)) return;
    seenScopes.add(scopeKey);
    scopes.push({ field, operator, value });
  };

  // Keep the exact legacy query for compatibility, then include canonical and keyed records.
  const legacyAliases = legacyLabAliases(currentLab);
  addScope("lab", "==", currentLab);
  addScope("lab", "==", canonicalizeLabName(currentLab));
  if (legacyAliases.length > 1) addScope("lab", "in", legacyAliases);
  addScope("labKey", "==", currentLabKey);
  addScope("createdByUid", "==", currentUser?.uid);

  const results = await Promise.allSettled(
    scopes.map(({ field, operator, value }) => getDocs(query(tcol, where(field, operator, value), limit(300)))),
  );
  const successful = results.filter((result) => result.status === "fulfilled");
  if (successful.length === 0) throw results[0]?.reason || new Error("Unable to load lab tickets.");

  results.forEach((result, index) => {
    if (result.status === "rejected") {
      console.warn(`Lab scope query unavailable (${scopes[index].field}).`, result.reason);
    }
  });

  const documents = new Map();
  successful.forEach(({ value }) => value.docs.forEach((ticketDoc) => documents.set(ticketDoc.id, ticketDoc)));
  return Array.from(documents.values());
}

function applyFiltersAndRender() {
  const s = (fSearch.value || "").trim().toLowerCase();
  const st = fStatus.value || "";
  const pr = fPriority.value || "";
  const lb = fLab.value || "";
  const cat = fCategory.value || "";

  let list = [...ticketsCache];
  if (st === "__OPEN__") {
     list = list.filter(t => t.status !== "CLOSED" && t.status !== "DONE");
  } else if (st) {
    list = list.filter(t => t.status === st);
  }
  if (pr) list = list.filter(t => t.priority === pr);
  if (lb) list = list.filter(t => normalizeLabKey(t.labKey || t.lab) === lb);
  if (cat) list = list.filter(t => t.category === cat);

  if (s) {
    list = list.filter(t => {
      const hay = [t.title, t.description, t.lab, t.category, ...(Array.isArray(t.tags)?t.tags:[])].join(" ").toLowerCase();
      return hay.includes(s);
    });
  }

  renderTicketsTable(list);
  ticketsCount.textContent = `Showing ${list.length} / ${ticketsCache.length}`;
}

function renderTicketsTable(list) {
  ticketsTbody.innerHTML = "";

  if (list.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="10" class="muted">No tickets.</td>`;
    ticketsTbody.appendChild(tr);
    return;
  }

  for (const t of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button type="button" class="ticket-open code" aria-label="Open ticket ${safeText(t.title || t.id)}">${safeText(t.id.slice(0,6))}</button></td>
      <td>${safeText(t.title || "-")}</td>
      <td class="code">${safeText(t.priority || "-")}</td>
      <td class="code">${safeText(t.status || "-")}</td>
      <td>${safeText(canonicalizeLabName(t.lab || t.labKey) || "-")}</td>
      <td>${safeText(t.category || "-")}</td>
      <td class="code">${safeText(t.procurementNeeded || "unknown")}</td>
      <td class="code">${safeText(t.expectedDeliveryDate || "-")}</td>
      <td class="code">${fmtDateTime(t.createdAt)}</td>
      <td class="code">${fmtDateTime(t.updatedAt)}</td>
    `;
    tr.onclick = () => selectTicket(t.id);
    tr.querySelector(".ticket-open").onclick = (event) => {
      event.stopPropagation();
      selectTicket(t.id);
    };
    ticketsTbody.appendChild(tr);
  }
}

/* ========= Details + Admin ========= */
async function selectTicket(ticketId) {
  selectedTicketId = ticketId;
  selectedIdPill.textContent = `#${ticketId.slice(0,8)}`;

  const t = ticketsCache.find(x => x.id === ticketId);
  renderDetails(t);

  commentInput.disabled = false;
  btnAddComment.disabled = false;
  commentsInfo.textContent = "Loading...";
  await loadComments(ticketId);
}

function renderDetails(t) {
  if (!t) {
    selectedIdPill.textContent = "—";
    ticketDetails.innerHTML = `<div class="muted">Select a ticket.</div>`;
    commentInput.disabled = true;
    btnAddComment.disabled = true;
    commentsInfo.textContent = "—";
    return;
  }

  const tags = Array.isArray(t.tags) ? t.tags.join(", ") : "";
  const canManageTickets = ["admin", "technician"].includes(currentRole);
  const commercialUrl = safeHttpUrl(t.commercialLink);
  const commercialLinkHtml = !t.commercialLink
    ? "-"
    : commercialUrl
      ? `<a href="${safeText(commercialUrl)}" target="_blank" rel="noreferrer">${safeText(t.commercialLink)}</a>`
      : `${safeText(t.commercialLink)} <span class="muted">(invalid link)</span>`;

  ticketDetails.innerHTML = `
    <div class="row space">
      <div>
        <div class="code">#${safeText(t.id)}</div>
        <div style="font-size:1.15rem; font-weight:650; margin-top:0.35rem;">${safeText(t.title || "")}</div>
        <div class="muted small" style="margin-top:0.4rem;">
          Created by <span class="code">${safeText(t.createdByEmail || "-")}</span>
          • Category <span class="code">${safeText(t.category || "-")}</span>
          • Lab <span class="code">${safeText(canonicalizeLabName(t.lab || t.labKey) || "-")}</span>
        </div>
      </div>
      <div class="row">
        <span class="pill code">${safeText(t.priority || "-")}</span>
        <span class="pill code">${safeText(t.status || "-")}</span>
      </div>
    </div>

    <hr class="sep"/>

    <div class="grid2">
      <div><div class="muted small">ETA</div><div class="code">${safeText(t.expectedDeliveryDate || "-")}</div></div>
      <div><div class="muted small">Category</div><div class="code">${safeText(t.category || "-")}</div></div>
      <div><div class="muted small">Hard deadline</div><div class="code">${t.hardDeadline?"yes":"no"}${t.hardDeadlineDate? " • "+safeText(t.hardDeadlineDate):""}</div></div>
      <div><div class="muted small">Commercially available</div><div class="code">${safeText(t.commerciallyAvailable || "unknown")}</div></div>
      <div><div class="muted small">Procurement needed</div><div class="code">${safeText(t.procurementNeeded || "unknown")}</div></div>
      <div><div class="muted small">Commercial link</div><div class="code">${commercialLinkHtml}</div></div>
      <div><div class="muted small">Deferred</div><div class="code">${safeText(t.canBeDeferred || "-")}${t.deferTo ? " • "+safeText(t.deferTo):""}</div></div>
      <div><div class="muted small">Why not deferred</div><div class="code">${safeText(t.whyNotDeferredCode || "-")}${t.whyNotDeferredText ? " • "+safeText(t.whyNotDeferredText):""}</div></div>
      <div><div class="muted small">Effort</div><div class="code">${safeText(t.effortGuess || "-")}</div></div>
      <div><div class="muted small">Tags</div><div class="code">${safeText(tags || "-")}</div></div>
    </div>

    <hr class="sep"/>

    <div><div class="muted small">Description</div><div style="white-space:pre-wrap; margin-top:0.35rem;">${safeText(t.description || "")}</div></div>
    <div style="margin-top:0.9rem;"><div class="muted small">DoD</div><div style="white-space:pre-wrap; margin-top:0.35rem;">${safeText(t.definitionOfDone || "")}</div></div>

    ${requesterSnapshotHtml(t)}
    ${canManageTickets ? ticketControlsHtml(t) : ""}
  `;

  if (canManageTickets) wireTicketControls(t);
  bindAutoDatePickers(ticketDetails);
}

function ticketControlsHtml(t) {
  const statusOptions = optionList(STATUS, t.status, { allowCustom: true });
  const prioOptions = optionList(PRIORITY, t.priority, { allowCustom: true });
  const categoryOptions = optionList(CATEGORIES, t.category, { allowCustom: true });
  const procurementOptions = optionList(YES_NO_UNKNOWN, t.procurementNeeded || "unknown");

  return `
    <hr class="sep"/>
    <div class="row space">
      <div class="muted small">Ticket controls</div>
      <div id="adminSaveMsg" class="msg" style="margin:0;"></div>
    </div>

    <div class="grid2">
      <label>
        ETA
        <input id="aExpected" type="date" value="${safeText(t.expectedDeliveryDate || "")}" />
      </label>
      <label>Category<select id="aCategory">${categoryOptions}</select></label>
      <label>Status<select id="aStatus">${statusOptions}</select></label>
      <label>Priority<select id="aPriority">${prioOptions}</select></label>
      <label>Assignee email<input id="aAssigneeEmail" type="text" value="${safeText(t.assigneeEmail || "")}" /></label>
      <label>Procurement needed<select id="aProcurement">${procurementOptions}</select></label>
      <label>Commercial link<input id="aCommercialLink" type="url" placeholder="https://..." value="${safeText(t.commercialLink || "")}" /></label>
      <label>Effort (technical)
        <select id="aEffort">
          <option value="" ${!t.effortAdmin?"selected":""}>(none)</option>
          <option value="S" ${t.effortAdmin==="S"?"selected":""}>S</option>
          <option value="M" ${t.effortAdmin==="M"?"selected":""}>M</option>
          <option value="L" ${t.effortAdmin==="L"?"selected":""}>L</option>
          <option value="XL" ${t.effortAdmin==="XL"?"selected":""}>XL</option>
        </select>
      </label>
      <label>Internal notes<input id="aNotes" type="text" value="${safeText(t.adminNotes || "")}" /></label>
    </div>

    <div class="row mt">
      <button id="btnAdminSave" class="btn primary">Save</button>
      <button id="btnSetTriage" class="btn">→ TRIAGE</button>
      <button id="btnSetInProgress" class="btn">→ IN_PROGRESS</button>
      <button id="btnSetDone" class="btn">→ DONE</button>
      <button id="btnSetClosed" class="btn">→ CLOSED</button>
    </div>
  `;
}

function wireTicketControls(t) {
  const adminSaveMsg = $("adminSaveMsg");
  const aStatus = $("aStatus");
  const aPriority = $("aPriority");
  const aCategory = $("aCategory");
  const aAssigneeEmail = $("aAssigneeEmail");
  const aProcurement = $("aProcurement");
  const aCommercialLink = $("aCommercialLink");
  const aEffort = $("aEffort");
  const aNotes = $("aNotes");
  const aExpected = $("aExpected");

  async function save(partial = {}) {
    try {
      await updateDoc(doc(db, "tickets", t.id), {
        requesterSubmission: t.requesterSubmission || buildRequesterSubmissionSnapshot(t),
        status: aStatus.value,
        priority: aPriority.value,
        category: aCategory.value || "",
        assigneeEmail: (aAssigneeEmail.value || "").trim(),
        procurementNeeded: aProcurement.value || "unknown",
        commercialLink: (aCommercialLink.value || "").trim(),
        effortAdmin: aEffort.value || "",
        adminNotes: (aNotes.value || "").trim(),
        ...partial,
        updatedAt: serverTimestamp(),
        expectedDeliveryDate: aExpected.value || "",
      });
      setMsg(adminSaveMsg, "Saved.", "ok");
      await refreshTickets();
      const still = ticketsCache.find(x => x.id === t.id);
      if (still) {
        renderDetails(still);
        await loadComments(t.id);
      }
    } catch (e) {
      setMsg(adminSaveMsg, `Save error: ${e?.message || e}`, "err");
    }
  }

  $("btnAdminSave").onclick = () => save();
  $("btnSetTriage").onclick = () => { aStatus.value = "TRIAGE"; save({ status: "TRIAGE" }); };
  $("btnSetInProgress").onclick = () => { aStatus.value = "IN_PROGRESS"; save({ status: "IN_PROGRESS" }); };
  $("btnSetDone").onclick = () => { aStatus.value = "DONE"; save({ status: "DONE" }); };
  $("btnSetClosed").onclick = () => { aStatus.value = "CLOSED"; save({ status: "CLOSED" }); };
}

/* ========= New ticket ========= */
function bindNewTicketDynamicLogic() {
  const tHardDeadline = $("tHardDeadline");
  const tHardDeadlineDate = $("tHardDeadlineDate");
  const tCommercially = $("tCommercially");
  const tCommercialLink = $("tCommercialLink");
  const tDeferred = $("tDeferred");
  const tDeferTo = $("tDeferTo");
  const tWhyNotCode = $("tWhyNotCode");
  const tWhyNotText = $("tWhyNotText");

  function apply() {
    if (tHardDeadline.value === "yes") tHardDeadlineDate.disabled = false;
    else { tHardDeadlineDate.value = ""; tHardDeadlineDate.disabled = true; }
    syncConditionalRequiredField(tHardDeadlineDate, tHardDeadline.value === "yes");

    if (tCommercially.value === "yes") tCommercialLink.disabled = false;
    else { tCommercialLink.value = ""; tCommercialLink.disabled = true; }
    syncConditionalRequiredField(tCommercialLink, tCommercially.value === "yes");

    if (tDeferred.value === "yes") {
      tDeferTo.disabled = false;
      tWhyNotCode.disabled = true;
      tWhyNotText.disabled = true;
      tWhyNotCode.value = "skills";
      tWhyNotText.value = "";
    } else {
      tDeferTo.value = "";
      tDeferTo.disabled = true;
      tWhyNotCode.disabled = false;
      tWhyNotText.disabled = false;
    }
    syncConditionalRequiredField(tDeferTo, tDeferred.value === "yes");
    syncConditionalRequiredField(tWhyNotCode, tDeferred.value !== "yes");
    syncConditionalRequiredField(tWhyNotText, false);
  }

  [tHardDeadline, tCommercially, tDeferred].forEach(el => el.addEventListener("change", apply));
  apply();
  return apply;
}
refreshNewTicketFormState = bindNewTicketDynamicLogic();

ticketForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  setMsg(formMsg, "Creating...");

  if (!currentUser) return;

  const selectedLab = resolvedLabSelection();
  const assignedLabUsesOtherOption = selectedLab.lab === OTHER_LAB_NAME
    && currentLab
    && !CANONICAL_LABS.includes(canonicalizeLabName(currentLab));
  const usesAssignedLab = ["postdoc", "phd"].includes(currentRole)
    && currentLab
    && (currentLabKey === selectedLab.labKey || assignedLabUsesOtherOption);
  const lab = usesAssignedLab ? currentLab : selectedLab.lab;
  const labKey = usesAssignedLab ? currentLabKey : selectedLab.labKey;
  const category = $("tCategory").value;
  const priority = $("tPriority").value;
  const expectedDeliveryDate = $("tExpected").value;

  const title = ($("tTitle").value || "").trim();
  const description = ($("tDescription").value || "").trim();
  const definitionOfDone = ($("tDoD").value || "").trim();

  const hardDeadline = $("tHardDeadline").value === "yes";
  const hardDeadlineDate = ($("tHardDeadlineDate").value || "").trim();

  const commerciallyAvailable = $("tCommercially").value;
  const commercialLink = ($("tCommercialLink").value || "").trim();
  const estimatedCostEUR = $("tCost").value ? Number($("tCost").value) : null;
  const procurementNeeded = $("tProcurement").value;

  const canBeDeferred = $("tDeferred").value;
  const deferTo = ($("tDeferTo").value || "").trim();

  const whyNotDeferredCode = $("tWhyNotCode").value;
  const whyNotDeferredText = ($("tWhyNotText").value || "").trim();

  const effortGuess = $("tEffort").value;

  const tags = ($("tTags").value || "").split(",").map(x => x.trim()).filter(Boolean).slice(0, 12);
  const requesterSubmission = buildRequesterSubmissionSnapshot({
    title,
    description,
    definitionOfDone,
    lab,
    labKey,
    category,
    priority,
    expectedDeliveryDate,
    hardDeadline,
    hardDeadlineDate,
    commerciallyAvailable,
    commercialLink,
    estimatedCostEUR,
    procurementNeeded,
    canBeDeferred,
    deferTo,
    whyNotDeferredCode,
    whyNotDeferredText,
    effortGuess,
    tags
  });

  if (!lab || !labKey || !title || !description || !definitionOfDone || !expectedDeliveryDate) { setMsg(formMsg, "Fill required fields.", "err"); return; }
  if (hardDeadline && !hardDeadlineDate) { setMsg(formMsg, "Hard deadline date required.", "err"); return; }
  if (commerciallyAvailable === "yes" && !commercialLink) { setMsg(formMsg, "Commercial link required.", "err"); return; }
  if (canBeDeferred === "yes" && !deferTo) { setMsg(formMsg, "If deferred=yes specify who.", "err"); return; }
  if (priority === "P0" && description.length < 40) { setMsg(formMsg, "P0 needs a specific description (>= 40 chars).", "err"); return; }

  try {
    await addDoc(collection(db, "tickets"), {
      title,
      description,
      definitionOfDone,
      lab,
      labKey,
      category,
      priority,
      status: "NEW",
      expectedDeliveryDate,
      hardDeadline,
      hardDeadlineDate: hardDeadline ? hardDeadlineDate : "",
      commerciallyAvailable,
      commercialLink: commerciallyAvailable === "yes" ? commercialLink : "",
      estimatedCostEUR,
      procurementNeeded,
      canBeDeferred,
      deferTo: canBeDeferred === "yes" ? deferTo : "",
      whyNotDeferredCode: canBeDeferred === "no" ? whyNotDeferredCode : "",
      whyNotDeferredText: canBeDeferred === "no" ? whyNotDeferredText : "",
      effortGuess,
      tags,
      requesterSubmission,
      requesterSubmittedAt: serverTimestamp(),
      createdByUid: currentUser.uid,
      createdByEmail: currentUser.email,
      assigneeEmail: "",
      createdAt: serverTimestamp(),
      updatedAt: serverTimestamp()
    });

    setMsg(formMsg, "Ticket created.", "ok");
    closeModal();
    await refreshTickets();
  } catch (e2) {
    setMsg(formMsg, `Create error: ${e2?.message || e2}`, "err");
  }
});

/* ========= Comments ========= */
btnAddComment.onclick = async () => {
  const text = (commentInput.value || "").trim();
  if (!text || !selectedTicketId) return;

  setMsg(commentMsg, "");
  commentInput.value = "";

  try {
    await addDoc(collection(db, "tickets", selectedTicketId, "comments"), {
      text,
      authorUid: currentUser.uid,
      authorEmail: currentUser.email,
      createdAt: serverTimestamp()
    });

    let metadataUpdateFailed = false;
    try {
      await updateDoc(doc(db, "tickets", selectedTicketId), {
        updatedAt: serverTimestamp(),
        lastComment: text,
        lastCommentAuthorEmail: currentUser.email || "",
        lastCommentAt: serverTimestamp(),
        commentCount: increment(1)
      });
    } catch (metaError) {
      metadataUpdateFailed = true;
      console.warn("Comment metadata update failed", metaError);
    }
    await loadComments(selectedTicketId);
    if (metadataUpdateFailed) {
      setMsg(commentMsg, "Comment saved, but ticket metadata update failed. Email notification may be delayed.", "err");
    } else {
      setMsg(commentMsg, "Comment sent. Notification email will follow.", "ok");
    }
  } catch (e) {
    setMsg(commentMsg, `Comment error: ${e?.message || e}`, "err");
  }
};

async function loadComments(ticketId) {
  commentsBox.innerHTML = `<div class="muted">Loading comments...</div>`;
  const snap = await getDocs(query(collection(db, "tickets", ticketId, "comments"), orderBy("createdAt", "asc"), limit(300)));
  const items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
  commentsInfo.textContent = `${items.length} comments`;
  renderComments(items);
}

function renderComments(items) {
  if (!items) {
    commentsBox.textContent = "Select a ticket.";
    commentsInfo.textContent = "—";
    return;
  }
  if (items.length === 0) {
    commentsBox.innerHTML = `<div class="muted">No comments yet.</div>`;
    return;
  }
  commentsBox.innerHTML = items.map(c => `
    <div class="commentItem">
      <div class="commentMeta">
        <span class="code">${safeText(c.authorEmail || "-")}</span>
        • <span class="code">${fmtDateTime(c.createdAt)}</span>
      </div>
      <div style="white-space:pre-wrap;">${safeText(c.text || "")}</div>
    </div>
  `).join("");
  commentsBox.scrollTop = commentsBox.scrollHeight;
}
