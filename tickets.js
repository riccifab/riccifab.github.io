import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

import {
  getFirestore,
  doc,
  getDoc,
  setDoc,
  collection,
  addDoc,
  query,
  where,
  orderBy,
  limit,
  getDocs,
  updateDoc,
  serverTimestamp
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";

/* =========================
   1) Firebase config (FILL ME)
   ========================= */
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

const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: "select_account" });

/* =========================
   2) UI refs
   ========================= */
const $ = (id) => document.getElementById(id);

const authGate = $("authGate");
const authMsg = $("authMsg");
const appSection = $("app");

const btnSignIn = $("btnSignIn");
const btnSignIn2 = $("btnSignIn2");
const btnSignOut = $("btnSignOut");
const btnNew = $("btnNew");
const userPill = $("userPill");

const roleInfo = $("roleInfo");
const lastRefresh = $("lastRefresh");
const btnRefresh = $("btnRefresh");

const fSearch = $("fSearch");
const fStatus = $("fStatus");
const fPriority = $("fPriority");
const fLab = $("fLab");
const fCategory = $("fCategory");

const ticketsTbody = $("ticketsTbody");
const ticketsCount = $("ticketsCount");

const ticketDetails = $("ticketDetails");
const commentsBox = $("comments");
const commentInput = $("commentInput");
const btnAddComment = $("btnAddComment");

const modal = $("modal");
const btnCloseModal = $("btnCloseModal");
const ticketForm = $("ticketForm");
const formMsg = $("formMsg");

/* =========================
   3) State
   ========================= */
let currentUser = null;
let currentRole = null; // "admin" | "pi"
let currentLab = null;
let ticketsCache = [];
let selectedTicketId = null;

const STATUS = ["NEW","TRIAGE","APPROVED","REJECTED","IN_PROGRESS","WAITING_ON_PI","WAITING_ON_PROCUREMENT","BLOCKED","DONE","CLOSED"];
const PRIORITY = ["P0","P1","P2","P3"];

/* =========================
   4) Helpers
   ========================= */
function setMsg(el, text, type = "") {
  el.textContent = text || "";
  el.classList.remove("ok", "err");
  if (type) el.classList.add(type);
}

function fmtDate(tsOrDate) {
  if (!tsOrDate) return "-";
  try {
    // Firestore Timestamp
    if (typeof tsOrDate.toDate === "function") {
      const d = tsOrDate.toDate();
      return d.toISOString().slice(0, 10);
    }
    // JS Date
    if (tsOrDate instanceof Date) return tsOrDate.toISOString().slice(0, 10);
    // string
    if (typeof tsOrDate === "string") return tsOrDate;
  } catch {}
  return "-";
}

function fmtDateTime(ts) {
  if (!ts) return "-";
  try {
    if (typeof ts.toDate === "function") {
      const d = ts.toDate();
      return d.toISOString().replace("T", " ").slice(0, 16);
    }
  } catch {}
  return "-";
}

function safeText(s) {
  return (s ?? "").toString().replace(/[<>]/g, "");
}

function openModal() {
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}
function closeModal() {
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  ticketForm.reset();
  setMsg(formMsg, "");
}

/* =========================
   5) Auth + allowlist
   ========================= */
async function ensureAllowedOrThrow(user) {
  const email = user?.email;
  if (!email) throw new Error("No email.");

  // allowlist doc id = email
  const ref = doc(db, "allowlist", email);
  const snap = await getDoc(ref);
  if (!snap.exists()) throw new Error("Not in allowlist.");

  const data = snap.data();
  const role = data.role || "pi";
  if (role !== "admin" && role !== "pi") throw new Error("Invalid role in allowlist.");

  currentRole = role;
  currentLab = data.lab || "";
  return { role, lab: currentLab };
}

async function doSignIn() {
  setMsg(authMsg, "Login in corso...");
  try {
    await signInWithPopup(auth, provider);
  } catch (e) {
    setMsg(authMsg, `Errore login: ${e?.message || e}`, "err");
  }
}

async function doSignOut() {
  await signOut(auth);
}

btnSignIn.onclick = doSignIn;
btnSignIn2.onclick = doSignIn;
btnSignOut.onclick = doSignOut;

onAuthStateChanged(auth, async (user) => {
  currentUser = user;

  if (!user) {
    // logged out
    authGate.classList.remove("hidden");
    appSection.classList.add("hidden");
    btnSignIn.classList.remove("hidden");
    btnSignOut.classList.add("hidden");
    btnNew.classList.add("hidden");
    userPill.classList.add("hidden");
    setMsg(authMsg, "");
    return;
  }

  setMsg(authMsg, "Verifica allowlist...");
  try {
    const { role, lab } = await ensureAllowedOrThrow(user);

    // Create/update users doc (optional, useful)
    const uref = doc(db, "users", user.uid);
    await setDoc(uref, {
      email: user.email,
      displayName: user.displayName || "",
      role,
      lab,
      lastLoginAt: serverTimestamp()
    }, { merge: true });

    // show app
    authGate.classList.add("hidden");
    appSection.classList.remove("hidden");

    btnSignIn.classList.add("hidden");
    btnSignOut.classList.remove("hidden");
    btnNew.classList.remove("hidden");

    userPill.classList.remove("hidden");
    userPill.textContent = `${user.email} • ${role}`;
    roleInfo.textContent = `Role: ${role}${lab ? " • Lab: " + lab : ""}`;

    // load tickets
    await refreshTickets();
  } catch (e) {
    setMsg(authMsg, `Accesso negato: ${e?.message || e}`, "err");
    // hard sign out
    await signOut(auth);
  }
});

/* =========================
   6) Tickets CRUD
   ========================= */
async function refreshTickets() {
  if (!currentUser) return;

  setMsg(ticketsCount, "Loading...");
  selectedTicketId = null;
  renderDetails(null);
  renderComments(null);

  // Base query (keep it simple to avoid indexes): last 200 tickets by updatedAt
  // For PI: only own tickets
  const tcol = collection(db, "tickets");

  let q;
  if (currentRole === "admin") {
    q = query(tcol, orderBy("updatedAt", "desc"), limit(200));
  } else {
    q = query(tcol, where("createdByUid", "==", currentUser.uid), orderBy("updatedAt", "desc"), limit(200));
  }

  const snap = await getDocs(q);
  ticketsCache = snap.docs.map(d => ({ id: d.id, ...d.data() }));

  // Populate lab filter options
  const labs = Array.from(new Set(ticketsCache.map(t => (t.lab || "").trim()).filter(Boolean))).sort();
  fLab.innerHTML = `<option value="">(all)</option>` + labs.map(l => `<option>${safeText(l)}</option>`).join("");

  applyFiltersAndRender();

  lastRefresh.textContent = new Date().toISOString().replace("T", " ").slice(0, 19);
}

btnRefresh.onclick = refreshTickets;

function applyFiltersAndRender() {
  const s = (fSearch.value || "").trim().toLowerCase();
  const st = fStatus.value || "";
  const pr = fPriority.value || "";
  const lb = fLab.value || "";
  const cat = fCategory.value || "";

  let list = [...ticketsCache];

  if (st) list = list.filter(t => t.status === st);
  if (pr) list = list.filter(t => t.priority === pr);
  if (lb) list = list.filter(t => (t.lab || "") === lb);
  if (cat) list = list.filter(t => t.category === cat);

  if (s) {
    list = list.filter(t => {
      const hay = [
        t.title, t.description, t.lab, t.category,
        ...(Array.isArray(t.tags) ? t.tags : [])
      ].join(" ").toLowerCase();
      return hay.includes(s);
    });
  }

  renderTicketsTable(list);
  ticketsCount.textContent = `Showing ${list.length} / ${ticketsCache.length}`;
}

[fSearch, fStatus, fPriority, fLab, fCategory].forEach(el => {
  el.addEventListener("input", applyFiltersAndRender);
  el.addEventListener("change", applyFiltersAndRender);
});

function badgeForPriority(p) {
  if (p === "P0") return "P0";
  if (p === "P1") return "P1";
  if (p === "P2") return "P2";
  return p || "-";
}

function renderTicketsTable(list) {
  ticketsTbody.innerHTML = "";

  for (const t of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="code">${safeText(t.id.slice(0,6))}</td>
      <td>${safeText(t.title || "-")}</td>
      <td class="code">${badgeForPriority(t.priority)}</td>
      <td class="code">${safeText(t.status || "-")}</td>
      <td>${safeText(t.lab || "-")}</td>
      <td class="code">${safeText(t.expectedDeliveryDate || "-")}</td>
      <td class="code">${fmtDateTime(t.createdAt)}</td>
      <td class="code">${fmtDateTime(t.updatedAt)}</td>
    `;
    tr.onclick = () => selectTicket(t.id);
    ticketsTbody.appendChild(tr);
  }

  if (list.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="8" class="muted">Nessun ticket.</td>`;
    ticketsTbody.appendChild(tr);
  }
}

async function selectTicket(ticketId) {
  selectedTicketId = ticketId;

  const t = ticketsCache.find(x => x.id === ticketId);
  renderDetails(t);

  // enable comments UI
  commentInput.disabled = false;
  btnAddComment.disabled = false;

  await loadComments(ticketId);
}

function renderDetails(t) {
  if (!t) {
    ticketDetails.innerHTML = `<div class="muted">Seleziona un ticket dalla tabella.</div>`;
    commentInput.disabled = true;
    btnAddComment.disabled = true;
    return;
  }

  const tags = Array.isArray(t.tags) ? t.tags.join(", ") : "";
  const isAdmin = currentRole === "admin";

  ticketDetails.innerHTML = `
    <div class="row space">
      <div>
        <div class="code">#${safeText(t.id)}</div>
        <div style="font-size:18px; font-weight:700; margin-top:6px;">${safeText(t.title || "")}</div>
        <div class="muted small" style="margin-top:6px;">
          Created by <span class="code">${safeText(t.createdByEmail || "-")}</span>
          • Category <span class="code">${safeText(t.category || "-")}</span>
          • Lab <span class="code">${safeText(t.lab || "-")}</span>
        </div>
      </div>
      <div class="row">
        <span class="pill code">${safeText(t.priority || "-")}</span>
        <span class="pill code">${safeText(t.status || "-")}</span>
      </div>
    </div>

    <hr class="sep"/>

    <div class="grid2">
      <div>
        <div class="muted small">Expected delivery</div>
        <div class="code">${safeText(t.expectedDeliveryDate || "-")}</div>
      </div>
      <div>
        <div class="muted small">Hard deadline</div>
        <div class="code">${t.hardDeadline ? "yes" : "no"} ${t.hardDeadlineDate ? " • " + safeText(t.hardDeadlineDate) : ""}</div>
      </div>

      <div>
        <div class="muted small">Commercially available</div>
        <div class="code">${safeText(t.commerciallyAvailable || "unknown")}</div>
      </div>
      <div>
        <div class="muted small">Commercial link</div>
        <div class="code">${t.commercialLink ? `<a href="${safeText(t.commercialLink)}" target="_blank" rel="noreferrer">${safeText(t.commercialLink)}</a>` : "-"}</div>
      </div>

      <div>
        <div class="muted small">Can be deferred</div>
        <div class="code">${safeText(t.canBeDeferred || "-")} ${t.deferTo ? " • " + safeText(t.deferTo) : ""}</div>
      </div>
      <div>
        <div class="muted small">If not, why</div>
        <div class="code">${safeText(t.whyNotDeferredCode || "-")} ${t.whyNotDeferredText ? " • " + safeText(t.whyNotDeferredText) : ""}</div>
      </div>

      <div>
        <div class="muted small">Impact / Effort</div>
        <div class="code">${safeText(t.impact || "-")} • ${safeText(t.effortGuess || "-")}</div>
      </div>
      <div>
        <div class="muted small">Tags</div>
        <div class="code">${safeText(tags || "-")}</div>
      </div>
    </div>

    <hr class="sep"/>

    <div>
      <div class="muted small">Description</div>
      <div style="white-space:pre-wrap; margin-top:6px;">${safeText(t.description || "")}</div>
    </div>

    <div style="margin-top:12px;">
      <div class="muted small">Definition of done</div>
      <div style="white-space:pre-wrap; margin-top:6px;">${safeText(t.definitionOfDone || "")}</div>
    </div>

    ${isAdmin ? adminControlsHtml(t) : ""}
  `;

  // Wire admin controls
  if (isAdmin) wireAdminControls(t);
}

function adminControlsHtml(t) {
  const statusOptions = STATUS.map(s => `<option value="${s}" ${t.status===s?"selected":""}>${s}</option>`).join("");
  const prioOptions = PRIORITY.map(p => `<option value="${p}" ${t.priority===p?"selected":""}>${p}</option>`).join("");

  return `
    <hr class="sep"/>
    <div class="row space">
      <h4 style="margin:0;">Admin controls</h4>
      <div id="adminSaveMsg" class="msg"></div>
    </div>

    <div class="grid2">
      <label>
        Status
        <select id="aStatus">${statusOptions}</select>
      </label>

      <label>
        Priority
        <select id="aPriority">${prioOptions}</select>
      </label>

      <label>
        Assignee (email)
        <input id="aAssigneeEmail" type="text" placeholder="fabio@..." value="${safeText(t.assigneeEmail || "")}" />
      </label>

      <label>
        Admin ETA date
        <input id="aAdminEta" type="date" value="${safeText(t.adminEtaDate || "")}" />
      </label>

      <label>
        Effort (admin)
        <select id="aEffort">
          <option value="S" ${t.effortAdmin==="S"?"selected":""}>S</option>
          <option value="M" ${t.effortAdmin==="M"?"selected":""}>M</option>
          <option value="L" ${t.effortAdmin==="L"?"selected":""}>L</option>
          <option value="XL" ${t.effortAdmin==="XL"?"selected":""}>XL</option>
        </select>
      </label>

      <label>
        Internal notes
        <input id="aNotes" type="text" placeholder="1 riga" value="${safeText(t.adminNotes || "")}" />
      </label>
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

function wireAdminControls(t) {
  const adminSaveMsg = $("adminSaveMsg");
  const aStatus = $("aStatus");
  const aPriority = $("aPriority");
  const aAssigneeEmail = $("aAssigneeEmail");
  const aAdminEta = $("aAdminEta");
  const aEffort = $("aEffort");
  const aNotes = $("aNotes");

  async function save(partial = {}) {
    try {
      const tref = doc(db, "tickets", t.id);
      await updateDoc(tref, {
        status: aStatus.value,
        priority: aPriority.value,
        assigneeEmail: (aAssigneeEmail.value || "").trim(),
        adminEtaDate: aAdminEta.value || "",
        effortAdmin: aEffort.value || "",
        adminNotes: (aNotes.value || "").trim(),
        ...partial,
        updatedAt: serverTimestamp()
      });
      setMsg(adminSaveMsg, "Saved.", "ok");
      await refreshTickets();
      // keep selected if still present
      const still = ticketsCache.find(x => x.id === t.id);
      if (still) {
        selectedTicketId = t.id;
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

/* =========================
   7) Create ticket (modal)
   ========================= */
btnNew.onclick = () => {
  setMsg(formMsg, "");
  openModal();
};
btnCloseModal.onclick = closeModal;
modal.addEventListener("click", (e) => {
  if (e.target === modal) closeModal();
});

ticketForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  setMsg(formMsg, "Creating...");

  const user = currentUser;
  if (!user) return;

  // Gather form
  const lab = ($("tLab").value || "").trim();
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

  const impact = $("tImpact").value;
  const effortGuess = $("tEffort").value;

  const tags = ($("tTags").value || "")
    .split(",")
    .map(x => x.trim())
    .filter(Boolean)
    .slice(0, 12);

  // Minimal validation
  if (!lab || !title || !description || !definitionOfDone || !expectedDeliveryDate) {
    setMsg(formMsg, "Compila i campi obbligatori.", "err");
    return;
  }
  if (priority === "P0") {
    // force a decent reason in description (basic check)
    if (description.length < 30) {
      setMsg(formMsg, "P0 richiede descrizione più specifica (min 30 char).", "err");
      return;
    }
  }
  if (hardDeadline && !hardDeadlineDate) {
    setMsg(formMsg, "Se hard deadline = yes, inserisci la data.", "err");
    return;
  }
  if (canBeDeferred === "yes" && !deferTo) {
    setMsg(formMsg, "Se deferred = yes, inserisci a chi.", "err");
    return;
  }
  if (canBeDeferred === "no" && !whyNotDeferredText) {
    setMsg(formMsg, "Se deferred = no, inserisci il perché (details).", "err");
    return;
  }

  try {
    const tcol = collection(db, "tickets");
    const docRef = await addDoc(tcol, {
      title,
      description,
      definitionOfDone,

      lab,
      category,
      priority,
      status: "NEW",

      expectedDeliveryDate,
      hardDeadline,
      hardDeadlineDate: hardDeadline ? hardDeadlineDate : "",

      commerciallyAvailable,
      commercialLink,
      estimatedCostEUR,
      procurementNeeded,

      canBeDeferred,
      deferTo: canBeDeferred === "yes" ? deferTo : "",
      whyNotDeferredCode: canBeDeferred === "no" ? whyNotDeferredCode : "",
      whyNotDeferredText: canBeDeferred === "no" ? whyNotDeferredText : "",

      impact,
      effortGuess,
      tags,

      createdByUid: user.uid,
      createdByEmail: user.email,
      assigneeEmail: "",

      createdAt: serverTimestamp(),
      updatedAt: serverTimestamp()
    });

    setMsg(formMsg, `Created: ${docRef.id}`, "ok");
    await refreshTickets();
    closeModal();
  } catch (e2) {
    setMsg(formMsg, `Create error: ${e2?.message || e2}`, "err");
  }
});

/* =========================
   8) Comments
   ========================= */
async function loadComments(ticketId) {
  commentsBox.innerHTML = `<div class="muted">Loading comments...</div>`;

  const ccol = collection(db, "tickets", ticketId, "comments");
  const q = query(ccol, orderBy("createdAt", "asc"), limit(200));
  const snap = await getDocs(q);

  const items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
  renderComments(items);
}

function renderComments(items) {
  if (!items) {
    commentsBox.textContent = "Seleziona un ticket.";
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

btnAddComment.onclick = async () => {
  const text = (commentInput.value || "").trim();
  if (!text) return;
  if (!selectedTicketId) return;

  commentInput.value = "";
  try {
    const ccol = collection(db, "tickets", selectedTicketId, "comments");
    await addDoc(ccol, {
      text,
      authorUid: currentUser.uid,
      authorEmail: currentUser.email,
      createdAt: serverTimestamp()
    });

    // also bump updatedAt (admin-only in rules, so best-effort)
    try {
      const tref = doc(db, "tickets", selectedTicketId);
      await updateDoc(tref, { updatedAt: serverTimestamp() });
    } catch {}

    await loadComments(selectedTicketId);
  } catch (e) {
    // If rules forbid comments you'll see it here
    alert(`Comment error: ${e?.message || e}`);
  }
};
