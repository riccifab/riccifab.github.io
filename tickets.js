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
  setDoc,
  collection,
  addDoc,
  getDocs,
  query,
  where,
  orderBy,
  limit,
  updateDoc,
  serverTimestamp
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";

/* =========================
   Firebase config (PASTE YOURS)
   ========================= */
// For Firebase JS SDK v7.20.0 and later, measurementId is optional
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

const sessionPill = $("sessionPill");
const pillDot = $("pillDot");
const pillText = $("pillText");

const authCard = $("authCard");
const authMsg = $("authMsg");

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

let currentUser = null;
let currentRole = null; // "admin" | "pi"
let currentLab = null;

let ticketsCache = [];
let selectedTicketId = null;

const STATUS = ["NEW","TRIAGE","APPROVED","REJECTED","IN_PROGRESS","WAITING_ON_PI","WAITING_ON_PROCUREMENT","BLOCKED","DONE","CLOSED"];
const PRIORITY = ["P0","P1","P2","P3"];

function setPill(state, text) {
  pillText.textContent = text;
  if (state === "in") {
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

function safeText(s) {
  return (s ?? "").toString().replace(/[<>]/g, "");
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

function openModal() {
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  setMsg(formMsg, "");
}
function closeModal() {
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  ticketForm.reset();
  setMsg(formMsg, "");
}

modal.addEventListener("click", (e) => {
  if (e.target === modal) closeModal();
});
btnCloseModal.addEventListener("click", closeModal);

async function ensureAllowedOrThrow(user) {
  const email = user?.email;
  if (!email) throw new Error("No email in auth user.");

  // allowlist/{email}
  const ref = doc(db, "allowlist", email);
  const snap = await getDoc(ref);
  if (!snap.exists()) throw new Error("Email not in allowlist.");

  const data = snap.data();
  const role = data.role || "pi";
  if (role !== "admin" && role !== "pi") throw new Error("Invalid role in allowlist.");
  currentRole = role;
  currentLab = data.lab || "";
  return { role, lab: currentLab };
}

/* =========================
   Auth actions
   ========================= */
btnSignIn.onclick = async () => {
  setMsg(authMsg, "Signing in...");
  try {
    const email = (loginEmail.value || "").trim();
    const pass = (loginPass.value || "").trim();
    if (!email || !pass) {
      setMsg(authMsg, "Insert email + password.", "err");
      return;
    }
    await signInWithEmailAndPassword(auth, email, pass);
  } catch (e) {
    setMsg(authMsg, `Login error: ${e?.message || e}`, "err");
  }
};

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

btnSignOut.onclick = async () => {
  await signOut(auth);
};

onAuthStateChanged(auth, async (user) => {
  currentUser = user;

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
    setPill("out", "Signed out");
    setMsg(authMsg, "");
    return;
  }

  setMsg(authMsg, "Checking allowlist...");
  try {
    const { role, lab } = await ensureAllowedOrThrow(user);

    // optional users/{uid} doc (admin-only in rules, but merge write can fail if rules block: ignore)
    try {
      await setDoc(doc(db, "users", user.uid), {
        email: user.email,
        displayName: user.displayName || "",
        role,
        lab,
        lastLoginAt: serverTimestamp()
      }, { merge: true });
    } catch {}

    authCard.classList.add("hidden");
    appSection.classList.remove("hidden");

    btnSignOut.classList.remove("hidden");
    btnSignIn.classList.add("hidden");
    btnReset.classList.add("hidden");

    setPill("in", `${user.email} • ${role}`);
    roleInfo.textContent = `Role: ${role}${lab ? " • Lab: " + lab : ""}`;

    await refreshTickets();
  } catch (e) {
    setMsg(authMsg, `Access denied: ${e?.message || e}`, "err");
    await signOut(auth);
  }
});

/* =========================
   Ticket list + filters
   ========================= */
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

  let snap;
  if (currentRole === "admin") {
    // admin: can sort server-side
    snap = await getDocs(query(tcol, orderBy("updatedAt", "desc"), limit(300)));
  } else {
    // PI: avoid composite indexes. Fetch own tickets and sort client-side.
    snap = await getDocs(query(tcol, where("createdByUid", "==", currentUser.uid), limit(300)));
  }

  ticketsCache = snap.docs.map(d => ({ id: d.id, ...d.data() }));

  // client-side sort by updatedAt desc (fallback to createdAt)
  ticketsCache.sort((a, b) => {
    const ta = a.updatedAt?.toMillis?.() ?? a.createdAt?.toMillis?.() ?? 0;
    const tb = b.updatedAt?.toMillis?.() ?? b.createdAt?.toMillis?.() ?? 0;
    return tb - ta;
  });

  // populate lab filter options
  const labs = Array.from(new Set(ticketsCache.map(t => (t.lab || "").trim()).filter(Boolean))).sort();
  fLab.innerHTML = `<option value="">(all)</option>` + labs.map(l => `<option>${safeText(l)}</option>`).join("");

  applyFiltersAndRender();
}

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

function renderTicketsTable(list) {
  ticketsTbody.innerHTML = "";

  if (list.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="8" class="muted">No tickets.</td>`;
    ticketsTbody.appendChild(tr);
    return;
  }

  for (const t of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="code">${safeText(t.id.slice(0,6))}</td>
      <td>${safeText(t.title || "-")}</td>
      <td class="code">${safeText(t.priority || "-")}</td>
      <td class="code">${safeText(t.status || "-")}</td>
      <td>${safeText(t.lab || "-")}</td>
      <td class="code">${safeText(t.expectedDeliveryDate || "-")}</td>
      <td class="code">${fmtDateTime(t.createdAt)}</td>
      <td class="code">${fmtDateTime(t.updatedAt)}</td>
    `;
    tr.onclick = () => selectTicket(t.id);
    ticketsTbody.appendChild(tr);
  }
}

/* =========================
   Ticket details + admin controls
   ========================= */
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
  const isAdmin = currentRole === "admin";

  ticketDetails.innerHTML = `
    <div class="row space">
      <div>
        <div class="code">#${safeText(t.id)}</div>
        <div style="font-size:1.15rem; font-weight:650; margin-top:0.35rem;">${safeText(t.title || "")}</div>
        <div class="muted small" style="margin-top:0.4rem;">
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
        <div class="code">${t.hardDeadline ? "yes" : "no"}${t.hardDeadlineDate ? " • " + safeText(t.hardDeadlineDate) : ""}</div>
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
        <div class="code">${safeText(t.canBeDeferred || "-")}${t.deferTo ? " • " + safeText(t.deferTo) : ""}</div>
      </div>
      <div>
        <div class="muted small">If not, why</div>
        <div class="code">${safeText(t.whyNotDeferredCode || "-")}${t.whyNotDeferredText ? " • " + safeText(t.whyNotDeferredText) : ""}</div>
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
      <div style="white-space:pre-wrap; margin-top:0.35rem;">${safeText(t.description || "")}</div>
    </div>

    <div style="margin-top:0.9rem;">
      <div class="muted small">Definition of done</div>
      <div style="white-space:pre-wrap; margin-top:0.35rem;">${safeText(t.definitionOfDone || "")}</div>
    </div>

    ${isAdmin ? adminControlsHtml(t) : ""}
  `;

  if (isAdmin) wireAdminControls(t);
}

function adminControlsHtml(t) {
  const statusOptions = STATUS.map(s => `<option value="${s}" ${t.status===s?"selected":""}>${s}</option>`).join("");
  const prioOptions = PRIORITY.map(p => `<option value="${p}" ${t.priority===p?"selected":""}>${p}</option>`).join("");

  return `
    <hr class="sep"/>
    <div class="row space">
      <div class="muted small">Admin controls</div>
      <div id="adminSaveMsg" class="msg" style="margin:0;"></div>
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
        Assignee email
        <input id="aAssigneeEmail" type="text" placeholder="fabio@..." value="${safeText(t.assigneeEmail || "")}" />
      </label>

      <label>
        Admin ETA date
        <input id="aAdminEta" type="date" value="${safeText(t.adminEtaDate || "")}" />
      </label>

      <label>
        Effort (admin)
        <select id="aEffort">
          <option value="" ${!t.effortAdmin?"selected":""}>(none)</option>
          <option value="S" ${t.effortAdmin==="S"?"selected":""}>S</option>
          <option value="M" ${t.effortAdmin==="M"?"selected":""}>M</option>
          <option value="L" ${t.effortAdmin==="L"?"selected":""}>L</option>
          <option value="XL" ${t.effortAdmin==="XL"?"selected":""}>XL</option>
        </select>
      </label>

      <label>
        Internal notes
        <input id="aNotes" type="text" placeholder="one-liner" value="${safeText(t.adminNotes || "")}" />
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

/* =========================
   New ticket creation
   ========================= */
btnNewTicket.onclick = () => openModal();

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
    // hard deadline
    if (tHardDeadline.value === "yes") {
      tHardDeadlineDate.disabled = false;
    } else {
      tHardDeadlineDate.value = "";
      tHardDeadlineDate.disabled = true;
    }

    // commercially available
    if (tCommercially.value === "yes") {
      tCommercialLink.disabled = false;
    } else {
      tCommercialLink.value = "";
      tCommercialLink.disabled = true;
    }

    // deferred logic
    if (tDeferred.value === "yes") {
      tDeferTo.disabled = false;
      tWhyNotCode.disabled = true;
      tWhyNotText.disabled = true;
      tWhyNotText.value = "";
    } else {
      tDeferTo.value = "";
      tDeferTo.disabled = true;
      tWhyNotCode.disabled = false;
      tWhyNotText.disabled = false;
    }
  }

  [tHardDeadline, tCommercially, tDeferred].forEach(el => el.addEventListener("change", apply));
  apply();
}
bindNewTicketDynamicLogic();

ticketForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  setMsg(formMsg, "Creating...");

  if (!currentUser) return;

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

  // validations
  if (!lab || !title || !description || !definitionOfDone || !expectedDeliveryDate) {
    setMsg(formMsg, "Fill required fields.", "err");
    return;
  }
  if (hardDeadline && !hardDeadlineDate) {
    setMsg(formMsg, "Hard deadline date required.", "err");
    return;
  }
  if (commerciallyAvailable === "yes" && !commercialLink) {
    setMsg(formMsg, "Commercial link required if commercially available = yes.", "err");
    return;
  }
  if (canBeDeferred === "yes" && !deferTo) {
    setMsg(formMsg, "If deferred = yes, specify who.", "err");
    return;
  }
  if (canBeDeferred === "no" && !whyNotDeferredText) {
    setMsg(formMsg, "If deferred = no, specify why (details).", "err");
    return;
  }
  if (priority === "P0" && description.length < 40) {
    setMsg(formMsg, "P0 needs a specific description (>= 40 chars).", "err");
    return;
  }

  try {
    const tcol = collection(db, "tickets");
    await addDoc(tcol, {
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
      commercialLink: commerciallyAvailable === "yes" ? commercialLink : "",
      estimatedCostEUR,
      procurementNeeded,

      canBeDeferred,
      deferTo: canBeDeferred === "yes" ? deferTo : "",
      whyNotDeferredCode: canBeDeferred === "no" ? whyNotDeferredCode : "",
      whyNotDeferredText: canBeDeferred === "no" ? whyNotDeferredText : "",

      impact,
      effortGuess,
      tags,

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

/* =========================
   Comments
   ========================= */
btnAddComment.onclick = async () => {
  const text = (commentInput.value || "").trim();
  if (!text) return;
  if (!selectedTicketId) return;

  setMsg(commentMsg, "");
  commentInput.value = "";

  try {
    const ccol = collection(db, "tickets", selectedTicketId, "comments");
    await addDoc(ccol, {
      text,
      authorUid: currentUser.uid,
      authorEmail: currentUser.email,
      createdAt: serverTimestamp()
    });

    // try bump ticket updatedAt (admin allowed; PI will fail silently)
    try {
      await updateDoc(doc(db, "tickets", selectedTicketId), { updatedAt: serverTimestamp() });
    } catch {}

    await loadComments(selectedTicketId);
  } catch (e) {
    setMsg(commentMsg, `Comment error: ${e?.message || e}`, "err");
  }
};

async function loadComments(ticketId) {
  commentsBox.innerHTML = `<div class="muted">Loading comments...</div>`;

  const ccol = collection(db, "tickets", ticketId, "comments");
  const snap = await getDocs(query(ccol, orderBy("createdAt", "asc"), limit(300)));

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

/* =========================
   Init UI defaults
   ========================= */
function renderComments(_) { /* overwritten above */ }
function renderDetails(_) { /* overwritten above */ }
// (keep placeholders removed by function hoisting – JS uses latest definitions)
