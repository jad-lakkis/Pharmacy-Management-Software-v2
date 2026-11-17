/* Pharmacy Management – single-page UI (no build step, no dependencies).
 * The server enforces every permission; the UI only hides what a user
 * cannot do (US1.2). */
"use strict";

// ---------------------------------------------------------------- helpers
const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } },
  del(k) { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
};

let state = { token: store.get("token"), me: null, task: store.get("task"), lastReceipt: null };

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") { logout(true); }
  if (!res.ok) throw new ApiError(res.status, data.error || `Request failed (${res.status})`);
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b || {});
const PATCH = (p, b) => api("PATCH", p, b || {});
const DEL = (p) => api("DELETE", p);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
// append/replace children, flattening arrays and skipping null/false
function add(el, ...children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function put(el, ...children) { el.replaceChildren(); return add(el, ...children); }
const money = (c) => (c === null || c === undefined) ? "" : `$${(c / 100).toFixed(2)}`;
const dt = (s) => s ? new Date(s).toLocaleString() : "";
const d = (s) => s ? String(s).slice(0, 10) : "";
const label = (s) => String(s || "").replaceAll("_", " ").toLowerCase().replace(/^./, (m) => m.toUpperCase());
const badge = (text, cls) => h("span", { class: `badge ${cls || text || ""}` }, label(text));
const has = (perm) => state.me && state.me.permissions.includes(perm);

function table(columns, rows, empty = "Nothing to show.") {
  if (!rows.length) return h("p", { class: "muted" }, empty);
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : "" }, c.title)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, columns.map((c) => h("td", { class: c.num ? "num" : "" }, c.render(r))))))));
}
function kv(pairs) {
  return h("dl", { class: "kv" }, Object.entries(pairs).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "")]));
}
function field(labelText, input, wide) { return h("label", { class: wide ? "wide" : "" }, labelText, input); }
function input(name, attrs = {}) { return h("input", { name, ...attrs }); }
function select(name, options, attrs = {}) {
  return h("select", { name, ...attrs }, options.map((o) => {
    const [value, text] = Array.isArray(o) ? o : [o, label(o)];
    return h("option", { value, selected: attrs.value !== undefined && String(attrs.value) === String(value) }, text);
  }));
}
function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else out[el.name] = el.value.trim() === "" ? null : el.value.trim();
  }
  return out;
}
function intOrNull(v) { return v === null || v === undefined || v === "" ? null : Number.parseInt(v, 10); }
function centsFromInput(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? Math.round(n * 100) : v;
}

// Flash messages inside the main panel
function flash(container, message, kind = "ok") {
  const box = container.querySelector(".flash-slot");
  if (!box) return;
  put(box, h("div", { class: `flash ${kind}` }, message));
}
async function act(container, fn, okMessage) {
  try {
    const result = await fn();
    if (okMessage) flash(container, typeof okMessage === "function" ? okMessage(result) : okMessage, "ok");
    return result;
  } catch (err) {
    flash(container, err.message, "error");
    throw err;
  }
}
const quiet = (p) => p.catch(() => {});

// ---------------------------------------------------------------- shell
const root = () => document.getElementById("app");

function logout(expired) {
  if (state.token && !expired) quiet(POST("/api/logout"));
  state = { token: null, me: null, task: null, lastReceipt: null };
  store.del("token"); store.del("task");
  renderLogin(expired ? "Your session ended. Please sign in again." : null);
}

async function renderLogin(message) {
  const form = h("form", { class: "form" },
    field("Username", input("username", { autocomplete: "username", required: true, autofocus: true })),
    field("Password", input("password", { type: "password", autocomplete: "current-password", required: true })),
    h("button", { class: "primary", type: "submit" }, "Sign in"));
  const slot = h("div", { class: "flash-slot" }, message ? h("div", { class: "flash error" }, message) : null);
  const demo = h("div", { class: "demo muted" });
  const box = h("div", { class: "login" }, h("h1", {}, "Pharmacy Management"), slot, form, demo);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const { username, password } = formData(form);
    try {
      const res = await POST("/api/login", { username, password });
      state.token = res.token; store.set("token", res.token);
      state.me = res;
      state.task = null;
      renderShell();
    } catch (err) { put(slot, h("div", { class: "flash error" }, err.message)); }
  });
  put(root(), box);
  try {
    const accounts = await GET("/api/demo-accounts");
    if (accounts.length) {
      add(demo, "Demo accounts: ", h("br"),
        accounts.map((a) => h("button", { type: "button", onclick: () => {
          form.username.value = a.username; form.password.value = a.password; form.requestSubmit();
        } }, a.label)));
    }
  } catch { /* demo accounts are optional */ }
}

async function boot() {
  if (!state.token) return renderLogin();
  try { state.me = await GET("/api/me"); renderShell(); } catch { /* logout() already shown */ }
}

function renderShell() {
  const me = state.me;
  const tasks = me.dashboard;
  if (!tasks.find((t) => t.key === state.task)) state.task = tasks.length ? tasks[0].key : null;
  const main = h("section", { class: "main" });
  const home = h("nav", { class: "home" },
    h("h1", {}, `Welcome, ${me.user.display_name}`),
    tasks.length ? tasks.map((t) => h("button", {
      class: `tile ${t.key === state.task ? "active" : ""}`, title: t.description,
      onclick: () => { state.task = t.key; store.set("task", t.key); renderShell(); },
    }, t.label, h("small", {}, t.description))) : h("p", { class: "muted" }, "No tasks are assigned to your account."));
  put(root(), 
    h("header", { class: "topbar" },
      h("strong", {}, "Pharmacy Management"), h("span", { class: "spacer" }),
      h("span", { class: "muted small" }, `${me.user.username} · ${label(me.user.role)}`),
      h("button", { onclick: () => logout(false) }, "Sign out")),
    h("div", { class: "layout" }, home, main));
  if (state.task) renderTask(main, state.task);
}

async function renderTask(main, key) {
  const view = VIEWS[key];
  put(main, h("div", { class: "flash-slot" }), h("p", { class: "muted" }, "Loading…"));
  if (!view) { put(main, h("p", {}, "This task has no screen yet.")); return; }
  const body = h("div", { class: "stack" });
  try {
    await view(body, () => renderTask(main, key));
    put(main, h("div", { class: "flash-slot" }), body);
  } catch (err) {
    put(main, h("div", { class: "flash error" }, err.message));
  }
}

// Each view(el, refresh) fills el. Actions call refresh() to reload.
const VIEWS = {};

// ------------------------------------------------- access (US1.1)
VIEWS.access = async (el, refresh) => {
  const [users, catalog] = await Promise.all([GET("/api/users"), GET("/api/permissions")]);
  const panel = () => el.closest(".main");
  const roles = Object.keys(catalog.roles);
  const addForm = h("form", { class: "form" },
    field("Username", input("username", { required: true })),
    field("Display name", input("display_name", { required: true })),
    field("Role", select("role", roles)),
    field("Temporary password", input("password", { type: "password", minlength: 8, required: true })),
    field("Patient id (patient accounts only)", input("patient_id", { placeholder: "PAT-…" })),
    h("button", { class: "primary", type: "submit" }, "Add Member"));
  addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await quiet(act(panel(), () => POST("/api/users", formData(addForm)), (u) => `Added ${u.username}`).then(refresh));
  });

  const rows = users.map((u) => {
    const roleSel = select("role", roles.filter((r) => (r === "PATIENT") === (u.role === "PATIENT")), { value: u.role });
    const permSel = select("permission", catalog.permissions);
    return { u, roleSel, permSel };
  });
  add(el, 
    h("h2", {}, "Access Control"),
    h("p", { class: "muted small" }, "Deactivating keeps the account's history in the audit log. Role changes reset individual overrides."),
    table([
      { title: "User", render: ({ u }) => [h("div", {}, u.display_name), h("div", { class: "muted small mono" }, u.username)] },
      { title: "Status", render: ({ u }) => u.active ? "Active" : badge("Deactivated", "HIGH") },
      { title: "Role", render: ({ u, roleSel }) => h("div", { class: "row" }, roleSel,
        h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/users/${u.id}/role`, { role: roleSel.value }), "Role updated").then(refresh)) }, "Modify Roles")) },
      { title: "Overrides", render: ({ u }) => [
        u.granted_permissions.map((p) => h("div", { class: "small" }, `+ ${p}`)),
        u.revoked_permissions.map((p) => h("div", { class: "small muted" }, `− ${p}`))] },
      { title: "Permission", render: ({ u, permSel }) => h("div", { class: "row" }, permSel,
        h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/users/${u.id}/permissions`, { action: "grant", permission: permSel.value }), "Granted").then(refresh)) }, "Grant"),
        h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/users/${u.id}/permissions`, { action: "revoke", permission: permSel.value }), "Revoked").then(refresh)) }, "Revoke")) },
      { title: "", render: ({ u }) => h("div", { class: "row" },
        u.active
          ? h("button", { class: "danger", onclick: () => { if (confirm(`Deactivate ${u.username}?`)) quiet(act(panel(), () => POST(`/api/users/${u.id}/deactivate`), "Deactivated").then(refresh)); } }, "Delete User")
          : h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/users/${u.id}/reactivate`), "Reactivated").then(refresh)) }, "Reactivate"),
        h("button", { onclick: () => { const pw = prompt(`New password for ${u.username} (8+ characters)`); if (pw) quiet(act(panel(), () => POST(`/api/users/${u.id}/password`, { password: pw }), "Password reset")); } }, "Reset password")) },
    ], rows),
    h("h3", {}, "Add Member"), addForm,
    h("h3", {}, "Role defaults"),
    table([{ title: "Role", render: (r) => label(r[0]) }, { title: "Permissions", render: (r) => r[1].join(", ") }],
      Object.entries(catalog.roles)));
};

// ------------------------------------------------- patients (US2.1)
function patientForm(p = {}) {
  return h("form", { class: "form" },
    field("First name", input("first_name", { value: p.first_name, required: true })),
    field("Last name", input("last_name", { value: p.last_name, required: true })),
    field("Date of birth", input("date_of_birth", { type: "date", value: p.date_of_birth, required: true })),
    field("Phone", input("phone", { value: p.phone })),
    field("Email", input("email", { type: "email", value: p.email })),
    field("Address", input("address", { value: p.address })),
    h("button", { class: "primary", type: "submit" }, p.id ? "Save changes" : "Create patient"));
}

VIEWS.patients = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const detail = h("div");
  const results = h("div");
  const search = input("q", { placeholder: "Search by name, phone or email" });
  async function runSearch() {
    const list = await GET(`/api/patients?q=${encodeURIComponent(search.value)}`);
    put(results, table([
      { title: "Patient", render: (p) => p.full_name },
      { title: "Date of birth", render: (p) => p.date_of_birth },
      { title: "Contact", render: (p) => [p.phone, p.email].filter(Boolean).join(" · ") },
      { title: "", render: (p) => h("button", { onclick: () => showPatient(p.id) }, "Open") },
    ], list, "No patients match."));
  }
  async function showPatient(id) {
    const p = await GET(`/api/patients/${id}`);
    const form = patientForm(p);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      await quiet(act(panel(), () => PATCH(`/api/patients/${id}`, formData(form)), "Patient updated").then(() => { showPatient(id); runSearch(); }));
    });
    const allergyForm = h("form", { class: "form" },
      field("Substance", input("substance", { required: true, placeholder: "e.g. penicillin" })),
      field("Reaction", input("reaction", { required: true })),
      field("Severity", select("severity", ["MILD", "MODERATE", "SEVERE"])),
      h("button", { type: "submit" }, "Add allergy"));
    allergyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      await quiet(act(panel(), () => POST(`/api/patients/${id}/allergies`, formData(allergyForm)), "Allergy recorded").then(() => showPatient(id)));
    });
    put(detail, h("div", { class: "card" },
      h("h3", {}, p.full_name), h("p", { class: "muted small mono" }, p.id), form,
      h("h3", {}, "Allergies"),
      table([
        { title: "Substance", render: (a) => a.substance },
        { title: "Reaction", render: (a) => a.reaction },
        { title: "Severity", render: (a) => badge(a.severity, { SEVERE: "HIGH", MODERATE: "MODERATE", MILD: "LOW" }[a.severity]) },
        { title: "", render: (a) => h("button", { class: "danger", onclick: () => quiet(act(panel(), () => DEL(`/api/patients/${id}/allergies/${encodeURIComponent(a.substance)}`), "Allergy removed").then(() => showPatient(id))) }, "Remove") },
      ], p.allergies, "No known allergies recorded."), allergyForm));
  }
  search.addEventListener("input", () => quiet(runSearch()));

  const newForm = patientForm();
  newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await quiet(act(panel(), () => POST("/api/patients", formData(newForm)), (p) => `Created ${p.full_name}`).then((p) => { newForm.reset(); runSearch(); showPatient(p.id); }));
  });

  const prescribers = await GET("/api/prescribers");
  const prescriberForm = h("form", { class: "form" },
    field("Name", input("name", { required: true })),
    field("Licence number", input("license_number", { required: true })),
    field("Phone", input("phone")), field("Specialty", input("specialty")), field("Clinic", input("clinic")),
    h("button", { type: "submit" }, "Add prescriber"));
  prescriberForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await quiet(act(panel(), () => POST("/api/prescribers", formData(prescriberForm)), "Prescriber added").then(refresh));
  });

  add(el, h("h2", {}, "Patients and prescribers"), search, results, detail,
    h("h3", {}, "New patient"), newForm,
    h("h3", {}, "Prescribers"),
    table([
      { title: "Name", render: (p) => p.name }, { title: "Licence", render: (p) => h("span", { class: "mono" }, p.license_number) },
      { title: "Specialty", render: (p) => p.specialty }, { title: "Clinic", render: (p) => p.clinic }, { title: "Phone", render: (p) => p.phone },
    ], prescribers),
    prescriberForm);
  await runSearch();
};

// ------------------------------------------------- clinical record (US2.2)
VIEWS.patient_record = async (el) => {
  const out = h("div");
  const search = input("q", { placeholder: "Search patient" });
  const results = h("div");
  async function runSearch() {
    const list = await GET(`/api/patients?q=${encodeURIComponent(search.value)}`);
    put(results, h("div", { class: "row" }, list.map((p) => h("button", { onclick: () => show(p.id) }, p.full_name))));
  }
  async function show(id) {
    const c = await GET(`/api/patients/${id}/clinical`);
    put(out, h("div", { class: "card" },
      h("h3", {}, c.patient.full_name),
      kv({ "Date of birth": c.patient.date_of_birth, Phone: c.patient.phone, Email: c.patient.email }),
      h("h3", {}, "Allergies"),
      c.allergies.length ? table([
        { title: "Substance", render: (a) => h("strong", {}, a.substance) }, { title: "Reaction", render: (a) => a.reaction },
        { title: "Severity", render: (a) => badge(a.severity, { SEVERE: "HIGH", MODERATE: "MODERATE", MILD: "LOW" }[a.severity]) },
        { title: "Recorded", render: (a) => d(a.recorded_at) },
      ], c.allergies) : h("p", {}, "No known allergies recorded."),
      h("h3", {}, "Medication history"),
      table([
        { title: "Rx", render: (m) => h("span", { class: "mono" }, m.prescription_id) },
        { title: "Medication", render: (m) => `${m.medication} ${m.strength || ""}` },
        { title: "Directions", render: (m) => m.directions },
        { title: "Qty", num: true, render: (m) => m.quantity },
        { title: "Status", render: (m) => label(m.status) },
        { title: "Written", render: (m) => m.written_date },
        { title: "Dispensed", render: (m) => d(m.dispensed_at) },
        { title: "Prescriber", render: (m) => m.prescriber },
      ], c.medication_history, "No prescriptions on file.")));
  }
  search.addEventListener("input", () => quiet(runSearch()));
  add(el, h("h2", {}, "Search Patient Record"), h("p", { class: "muted small" }, "Each view of a clinical record is written to the audit log."), search, results, out);
  await runSearch();
};

// ------------------------------------------------- workflow (US3.1, US3.2)
const NOTE_TARGETS = { ON_HOLD: "Reason for hold", CANCELLED: "Reason for cancelling", DATA_ENTERED_RETURN: "What needs correcting?" };
async function moveRx(panel, rx, to, refresh) {
  let note = null;
  const isReturn = rx.status === "AWAITING_VERIFICATION" && to === "DATA_ENTERED";
  const ask = isReturn ? NOTE_TARGETS.DATA_ENTERED_RETURN : (rx.status !== "ON_HOLD" || to === "CANCELLED") && NOTE_TARGETS[to];
  if (ask) { note = prompt(ask); if (!note) return; }
  await quiet(act(panel, () => POST(`/api/prescriptions/${rx.id}/transition`, { to, note }), `${rx.id} → ${label(to)}`).then(refresh));
}
function transitionButtons(panel, rx, refresh) {
  return h("div", { class: "row" }, rx.allowed_transitions.map((to) => {
    const text = rx.status === "ON_HOLD" && to !== "CANCELLED" ? `Resume (${label(to)})`
      : rx.status === "AWAITING_VERIFICATION" && to === "DATA_ENTERED" ? "Return for correction" : label(to);
    return h("button", { class: to === "CANCELLED" ? "danger" : (to === "VERIFIED" || to === "DISPENSED") ? "primary" : "",
      onclick: () => moveRx(panel, rx, to, refresh) }, text);
  }));
}
function historyList(rx) {
  return h("ul", { class: "timeline" }, rx.status_history.map((c) => h("li", {}, `${dt(c.at)} — ${label(c.to_status)}${c.note ? `: ${c.note}` : ""}`)));
}

VIEWS.workflow = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const [patients, prescribers, products] = await Promise.all([GET("/api/patients"), GET("/api/prescribers"), GET("/api/products")]);
  const filter = select("status", [["", "All active"], "RECEIVED", "DATA_ENTERED", "AWAITING_VERIFICATION", "VERIFIED", "DISPENSED", "ON_HOLD", "PICKED_UP", "CANCELLED"], { value: store.get("rxFilter") || "" });
  const list = h("div");
  async function load() {
    store.set("rxFilter", filter.value);
    let rxs = await GET(`/api/prescriptions${filter.value ? `?status=${filter.value}` : ""}`);
    if (!filter.value) rxs = rxs.filter((r) => !["PICKED_UP", "CANCELLED"].includes(r.status));
    put(list, rxs.length ? rxs.map((rx) => h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, rx.id), badge(rx.status, ""), rx.refill_request_id ? badge(`Refill ${rx.refill_request_id}`, "LOW") : null),
      kv({ Patient: rx.patient_name, Medication: rx.medication, Quantity: `${rx.quantity} (${rx.daily_quantity}/day, ${rx.days_supply} days)`,
        Directions: rx.directions, Prescriber: rx.prescriber_name, Refills: `${rx.refills_remaining} of ${rx.refills_authorized} remaining`,
        Verified: rx.verified_at ? dt(rx.verified_at) : "Not yet",
        Lots: rx.allocations.map((a) => `${a.lot_number} ×${a.quantity} (exp ${a.expiry_date})`).join(", ") }),
      transitionButtons(panel(), rx, load),
      h("details", {}, h("summary", { class: "small muted" }, "Status history"), historyList(rx))))
      : h("p", { class: "muted" }, "No prescriptions in this stage."));
  }
  filter.addEventListener("change", () => quiet(load()));

  const form = h("form", { class: "form" },
    field("Patient", select("patient_id", patients.map((p) => [p.id, `${p.full_name} (${p.date_of_birth})`]))),
    field("Prescriber", select("prescriber_id", prescribers.map((p) => [p.id, p.name]))),
    field("Medication", select("product_id", products.map((p) => [p.id, `${p.name} ${p.strength} ${p.dosage_form}`]))),
    field("Quantity", input("quantity", { type: "number", min: 1, required: true })),
    field("Units per day", input("daily_quantity", { type: "number", min: 1, required: true })),
    field("Refills authorised", input("refills_authorized", { type: "number", min: 0, max: 11, value: 0 })),
    field("Written date", input("written_date", { type: "date" })),
    field("Directions", input("directions", { required: true }), true),
    h("button", { class: "primary", type: "submit" }, "Enter prescription"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(form);
    ["quantity", "daily_quantity", "refills_authorized"].forEach((k) => { data[k] = intOrNull(data[k]); });
    await quiet(act(panel(), () => POST("/api/prescriptions", data), (rx) => `Created ${rx.id} (received)`).then(() => { form.reset(); load(); }));
  });

  add(el, h("h2", {}, "Workflow management"),
    h("p", { class: "muted small" }, "Received → Data entered → Awaiting verification → Verified (pharmacist) → Dispensed → Picked up (after payment)."),
    h("details", {}, h("summary", {}, "Enter a new prescription"), form),
    h("div", { class: "row" }, "Show:", filter), list);
  await load();
};

// ------------------------------------------------- alerts (US4.1, US4.2)
function alertCard(panel, a, onResolved, guidance) {
  const outcome = select("outcome", Object.keys(guidance || {}).length ? Object.keys(guidance) : ["OVERRIDE_CONTINUE", "PRESCRIBER_CONFIRMED", "PRESCRIBER_CHANGED_THERAPY", "DISCONTINUE"]);
  const hint = h("p", { class: "muted small" }, guidance ? guidance[outcome.value] : "");
  outcome.addEventListener("change", () => { hint.textContent = guidance ? guidance[outcome.value] : ""; });
  const form = h("form", { class: "form" }, field("Outcome", outcome), field("Rationale (min. 10 characters)", h("textarea", { name: "rationale", required: true, minlength: 10 }), true), hint,
    h("button", { class: "primary", type: "submit" }, "Record decision"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await quiet(act(panel, () => POST(`/api/alerts/${a.id}/resolve`, formData(form)), "Decision recorded").then(onResolved));
  });
  return h("div", { class: `card alert-${a.severity}` },
    h("div", { class: "row" }, badge(a.severity), h("strong", {}, label(a.alert_type)), h("span", { class: "muted small mono" }, `${a.id} · ${a.prescription_id}`)),
    h("p", {}, a.message),
    h("h3", {}, "Triggering data"), kv(Object.fromEntries(Object.entries(a.triggering_data).map(([k, v]) => [label(k), v]))),
    h("h3", {}, "Reference basis"), h("p", { class: "small" }, a.reference_basis),
    a.status === "OPEN" ? form : h("div", { class: "small" },
      h("strong", {}, `Resolved: ${label(a.resolution.outcome)}`), h("div", {}, a.resolution.rationale),
      h("div", { class: "muted" }, dt(a.resolution.resolved_at))));
}

VIEWS.verification = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const [queue, guidance] = await Promise.all([GET("/api/verification-queue"), GET("/api/alerts/guidance")]);
  add(el, h("h2", {}, "Verification and alerts"),
    h("p", { class: "muted small" }, "A prescription cannot be verified while an alert is open, or after an alert was resolved as discontinue / therapy changed."));
  if (!queue.length) add(el, h("p", { class: "muted" }, "No prescriptions are waiting for verification."));
  for (const item of queue) {
    const rx = item.prescription;
    add(el, h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, rx.id), h("span", {}, `${rx.patient_name} — ${rx.medication}`),
        item.open_alerts ? badge(`${item.open_alerts} open alert${item.open_alerts > 1 ? "s" : ""}`, "HIGH") : badge("No open alerts", "LOW")),
      kv({ Quantity: `${rx.quantity} (${rx.daily_quantity}/day)`, Directions: rx.directions, Prescriber: rx.prescriber_name }),
      item.alerts.map((a) => alertCard(panel(), a, refresh, guidance)),
      item.blockers.length ? h("ul", { class: "small" }, item.blockers.map((b) => h("li", {}, b))) : null,
      transitionButtons(panel(), rx, refresh)));
  }
};

VIEWS.alert_form = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const [open, guidance, rules] = await Promise.all([GET("/api/alerts?status=OPEN"), GET("/api/alerts/guidance"), GET("/api/interaction-rules")]);
  add(el, h("h2", {}, "Decision-making form"),
    h("details", {}, h("summary", {}, "Info: Making a form"),
      h("p", { class: "small" }, "Choose the outcome that describes what you decided, and write the clinical reasoning so another pharmacist could follow it. The decision, your name and the time are stored permanently in the audit log."),
      kv(Object.fromEntries(Object.entries(guidance).map(([k, v]) => [label(k), v])))),
    open.length ? open.map((a) => alertCard(panel(), a, refresh, guidance)) : h("p", { class: "muted" }, "No open alerts."),
    h("h3", {}, "Interaction reference table"),
    table([{ title: "Ingredients", render: (r) => `${r.ingredient_a} + ${r.ingredient_b}` }, { title: "Severity", render: (r) => badge(r.severity) },
      { title: "Effect", render: (r) => r.description }, { title: "Source", render: (r) => h("span", { class: "small" }, r.reference_source) }], rules));
};

// ------------------------------------------------- refill review (pharmacist)
VIEWS.refill_review = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const queue = await GET("/api/refills/queue");
  add(el, h("h2", {}, "Refill requests"),
    h("p", { class: "muted small" }, "Approving creates a new fill that goes through data entry, screening and verification as usual."),
    queue.length ? queue.map((r) => h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, r.id), badge(r.status, ""), h("span", {}, `${r.patient} — ${r.medication}`)),
      kv({ Prescription: r.prescription_id, "Refills remaining": r.refills_remaining, Pickup: r.pickup_branch, Requested: dt(r.created_at), "Patient note": r.patient_note }),
      h("div", { class: "row" },
        r.status === "SUBMITTED" ? h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/refills/${r.id}/start-review`), "Review started").then(refresh)) }, "Start review") : null,
        h("button", { class: "primary", onclick: () => quiet(act(panel(), () => POST(`/api/refills/${r.id}/approve`), (x) => `Approved; fill ${x.fill_prescription_id} created`).then(refresh)) }, "Approve"),
        h("button", { class: "danger", onclick: () => { const reason = prompt("Reason for not approving"); if (reason) quiet(act(panel(), () => POST(`/api/refills/${r.id}/reject`, { reason }), "Rejected").then(refresh)); } }, "Reject"))))
      : h("p", { class: "muted" }, "No refill requests waiting."));
};

// ------------------------------------------------- products (add a new medicine to the catalogue)
VIEWS.products = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const products = await GET("/api/products");
  const form = h("form", { class: "form" },
    field("Name", input("name", { required: true, placeholder: "e.g. Metformin" })),
    field("Barcode (GTIN, 8/12/13/14 digits)", input("gtin", { required: true, class: "mono", inputmode: "numeric" })),
    field("Active ingredients (comma separated)", input("ingredients", { required: true, placeholder: "e.g. metformin" })),
    field("Strength", input("strength", { required: true, placeholder: "e.g. 500 mg" })),
    field("Dosage form", input("dosage_form", { required: true, placeholder: "e.g. tablet" })),
    field("Selling price per unit ($)", input("unit_price", { type: "number", min: "0", step: "0.01", required: true })),
    field("Cost per unit ($)", input("unit_cost", { type: "number", min: "0", step: "0.01", required: true })),
    field("Reorder point", input("reorder_point", { type: "number", min: 0, value: 0 })),
    field("Safety stock", input("safety_stock", { type: "number", min: 0, value: 0 })),
    field("Supplier lead time (days)", input("lead_time_days", { type: "number", min: 0, max: 365, value: 7 })),
    field("Max units per day (optional)", input("max_daily_quantity", { type: "number", min: 1 })),
    h("label", { class: "wide" }, h("input", { type: "checkbox", name: "requires_prescription", checked: true }), " Requires a prescription"),
    h("button", { class: "primary", type: "submit" }, "Add product"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = formData(form);
    const body = {
      name: f.name, gtin: f.gtin, strength: f.strength, dosage_form: f.dosage_form,
      ingredients: (f.ingredients || "").split(",").map((s) => s.trim()).filter(Boolean),
      unit_price_cents: centsFromInput(f.unit_price), unit_cost_cents: centsFromInput(f.unit_cost),
      requires_prescription: f.requires_prescription,
      reorder_point: intOrNull(f.reorder_point) ?? 0, safety_stock: intOrNull(f.safety_stock) ?? 0,
      lead_time_days: intOrNull(f.lead_time_days) ?? 7, max_daily_quantity: intOrNull(f.max_daily_quantity),
    };
    const main = panel();
    // refresh() redraws the panel (and its message area), so show the hint after it.
    await quiet(act(main, () => POST("/api/products", body))
      .then((p) => refresh().then(() => flash(main, `Added ${p.name}. Receive its first stock under "Check by Bar Code".`, "ok"))));
  });

  add(el, h("h2", {}, "Products"),
    h("p", { class: "muted small" }, "Add a medicine that is not in the catalogue yet. After that, staff can receive stock for it, and it appears in inventory, reorder suggestions, prescriptions and sales."),
    table([
      { title: "Product", render: (p) => `${p.name} ${p.strength} ${p.dosage_form}` },
      { title: "Barcode", render: (p) => h("span", { class: "mono small" }, p.gtin) },
      { title: "Price", num: true, render: (p) => money(p.unit_price_cents) },
      { title: "Cost", num: true, render: (p) => money(p.unit_cost_cents) },
      { title: "Rx", render: (p) => p.requires_prescription ? "Yes" : "No (over the counter)" },
      { title: "Reorder point", num: true, render: (p) => p.reorder_point },
    ], products, "No products yet."),
    h("h3", {}, "Add product"), form);
};

// ------------------------------------------------- receiving (US5.1)
VIEWS.receive = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const products = await GET("/api/products");
  const preview = h("div");
  const scanForm = h("form", { class: "form" },
    field("Scan barcode", input("scan", { required: true, autofocus: true, placeholder: "(01)…(17)YYMMDD(10)LOT", class: "mono" }), true),
    field("Quantity", input("quantity", { type: "number", min: 1, required: true })),
    field("Lot (if not in barcode)", input("lot_number")),
    field("Expiry (if not in barcode)", input("expiry_date", { type: "date" })),
    field("Supplier", input("supplier")),
    h("div", { class: "row" }, h("button", { type: "button", onclick: doPreview }, "Check by Bar Code"), h("button", { class: "primary", type: "submit" }, "Receive")));
  async function doPreview() {
    const scan = scanForm.scan.value;
    try {
      const p = await POST("/api/inventory/scan/preview", { scan });
      put(preview, h("div", { class: "card" }, kv({
        Product: `${p.product.name} ${p.product.strength} ${p.product.dosage_form}`, GTIN: h("span", { class: "mono" }, p.gtin),
        Lot: p.lot_number || h("em", {}, "not in barcode – type it"), Expiry: p.expiry_date || h("em", {}, "not in barcode – enter it") })));
    } catch (err) { put(preview, h("div", { class: "flash error" }, err.message)); }
  }
  scanForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(scanForm); data.quantity = intOrNull(data.quantity);
    await quiet(act(panel(), () => POST("/api/inventory/receive-scan", data), (lot) => `Received ${data.quantity} × lot ${lot.lot_number} (now ${lot.quantity_on_hand} on hand)`)
      .then(() => { scanForm.reset(); put(preview); scanForm.scan.focus(); }));
  });

  const manual = h("form", { class: "form" },
    field("Product", select("product_id", products.map((p) => [p.id, `${p.name} ${p.strength}`]))),
    field("Enter Barcode / lot", input("lot_number", { required: true })),
    field("Expiry", input("expiry_date", { type: "date", required: true })),
    field("Quantity", input("quantity", { type: "number", min: 1, required: true })),
    field("Supplier", input("supplier")),
    h("button", { type: "submit" }, "Receive"));
  manual.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(manual); data.quantity = intOrNull(data.quantity);
    await quiet(act(panel(), () => POST("/api/inventory/receive", data), "Received").then(() => manual.reset()));
  });

  const yy = String(new Date().getFullYear() + 1).slice(2);
  add(el, h("h2", {}, "Check by Bar Code"),
    h("p", { class: "muted small" }, "Scanners that send GS1 DataMatrix data are supported. You can also type the human-readable form, or use | for the group separator."),
    scanForm, preview,
    h("details", {}, h("summary", { class: "small" }, "Example scans for the demo products"),
      table([{ title: "Product", render: (p) => `${p.name} ${p.strength}` },
        { title: "Scan text", render: (p) => h("span", { class: "mono" }, `(01)${p.gtin}(17)${yy}1231(10)NEW${p.gtin.slice(-4)}`) }], products)),
    h("h3", {}, "Enter Barcode manually"), manual);
};

// ------------------------------------------------- inventory and alerts (US5.2)
VIEWS.inventory = async (el) => {
  const rows = await GET("/api/inventory");
  add(el, h("h2", {}, "Inventory"), table([
    { title: "Product", render: (r) => `${r.product.name} ${r.product.strength}` },
    { title: "On hand", num: true, render: (r) => r.below_reorder_point ? h("strong", {}, r.on_hand) : r.on_hand },
    { title: "Reorder point", num: true, render: (r) => r.reorder_point },
    { title: "Expired on shelf", num: true, render: (r) => r.expired_on_shelf || "" },
    { title: "Lots (first to expire first)", render: (r) => r.lots.map((l) => h("div", { class: "small" }, `${l.lot_number}: ${l.quantity_on_hand} · exp ${l.expiry_date}`)) },
  ], rows));
};

VIEWS.inventory_alerts = async (el, refresh) => {
  const days = input("days", { type: "number", min: 1, max: 730, value: store.get("expDays") || 90 });
  const list = h("div");
  async function load() {
    store.set("expDays", days.value);
    const alerts = await GET(`/api/inventory/alerts?days=${encodeURIComponent(days.value)}`);
    put(list, table([
      { title: "Severity", render: (a) => badge(a.severity) },
      { title: "Alert", render: (a) => label(a.type) },
      { title: "Product", render: (a) => a.product },
      { title: "Details", render: (a) => a.message },
      { title: "Values", render: (a) => h("span", { class: "small muted" }, a.lot_number ? `lot ${a.lot_number}, ${a.quantity} units, expires ${a.expiry_date}` : `on hand ${a.on_hand}, reorder point ${a.reorder_point}`) },
    ], alerts, "No stock alerts."));
  }
  days.addEventListener("change", () => quiet(load()));
  add(el, h("h2", {}, "Stock alerts"), h("div", { class: "row" }, "Near-expiry window (days):", days), list);
  await load();
};

// ------------------------------------------------- purchasing (US6.1, US6.2)
VIEWS.reorder = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const showAll = h("input", { type: "checkbox", checked: store.get("reorderAll") === "1" });
  const list = h("div");
  // Optional: draft the order for a listed supplier so its price list is used.
  let suppliers = [];
  try { suppliers = await GET("/api/suppliers"); } catch { /* supplier list is optional */ }
  const supplierSel = suppliers.length
    ? select("supplier_id", [["", "Default supplier (product default costs)"], ...suppliers.map((s) => [s.id, s.name])])
    : null;
  async function load() {
    store.set("reorderAll", showAll.checked ? "1" : "0");
    const rows = await GET(`/api/reorder-suggestions${showAll.checked ? "?all=1" : ""}`);
    const checks = new Map();
    put(list, 
      rows.length ? rows.map((r) => {
        const cb = h("input", { type: "checkbox", checked: r.triggered, disabled: !r.triggered });
        checks.set(r.product_id, cb);
        return h("div", { class: "card" },
          h("div", { class: "row" }, cb, h("strong", {}, r.product), r.triggered ? badge(`Order ${r.suggested_quantity}`, "MODERATE") : badge("No order needed", "LOW"),
            r.pending_approval ? badge(`${r.pending_approval} pending approval`, "") : null),
          h("div", { class: "stats" }, [
            ["Units used", `${r.units_used_in_window} in ${r.usage_window_days} d`], ["Avg daily usage", r.avg_daily_usage],
            ["Lead time", `${r.lead_time_days} d`], ["Review period", `${r.review_period_days} d`], ["Safety stock", r.safety_stock],
            ["On hand", r.on_hand], ["On order", r.on_order], ["Reorder point", r.reorder_point], ["Target stock", r.target_stock],
            ["Estimated cost", money(r.estimated_cost_cents)],
          ].map(([k, v]) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, k)))),
          h("p", { class: "small mono" }, r.explanation));
      }) : h("p", { class: "muted" }, "Nothing needs reordering."),
      rows.some((r) => r.triggered) ? h("div", { class: "row" },
        supplierSel ? h("label", { class: "row" }, "Supplier", supplierSel) : null,
        h("button", { class: "primary", onclick: () => {
          const ids = [...checks].filter(([, cb]) => cb.checked).map(([id]) => id);
          const body = { product_ids: ids };
          if (supplierSel && supplierSel.value) body.supplier_id = supplierSel.value;
          quiet(act(panel(), () => POST("/api/purchase-orders", body), (po) => `Drafted ${po.id}; it now needs approval in Purchase Inventory`).then(load));
        } }, "Create purchase order for selected")) : null,
      h("p", { class: "muted small" }, rows[0] ? rows[0].formula : ""));
  }
  showAll.addEventListener("change", () => quiet(load()));
  add(el, h("h2", {}, "Recommended: Reorder"), h("label", { class: "row" }, showAll, "Show every product, including those that do not need ordering"), list);
  await load();
};

VIEWS.purchase_orders = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const orders = await GET("/api/purchase-orders");
  add(el, h("h2", {}, "Purchase Inventory"),
    h("p", { class: "muted small" }, "Suggested orders are never placed automatically. Approve, change quantities (with a reason) or reject."));
  if (!orders.length) add(el, h("p", { class: "muted" }, "No purchase orders yet. Create one from Recommended: Reorder."));
  for (const po of orders) {
    const qtyInputs = new Map();
    const reason = h("input", { name: "reason", placeholder: "Reason (required to modify or reject)" });
    const pending = po.status === "PENDING_APPROVAL";
    add(el, h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, po.id), badge(po.status, pending ? "MODERATE" : ""), h("span", { class: "muted small" }, `${po.supplier} · ${dt(po.created_at)}`)),
      table([
        { title: "Product", render: (l) => l.product_name },
        { title: "Suggested", num: true, render: (l) => l.suggested_quantity },
        { title: "Quantity", num: true, render: (l) => {
          if (!pending) return l.quantity;
          const inp = h("input", { type: "number", min: 0, value: l.quantity, class: "narrow" });
          qtyInputs.set(l.product_id, inp); return inp; } },
        { title: "Unit cost", num: true, render: (l) => [money(l.unit_cost_cents),
          l.basis.unit_cost_source ? h("div", { class: "muted small" }, l.basis.unit_cost_source) : null] },
        { title: "Basis", render: (l) => h("span", { class: "small mono" }, l.basis.explanation) },
      ], po.lines),
      h("p", {}, `Total: ${money(po.total_cost_cents)}`),
      po.decision ? h("p", { class: "small" }, `${label(po.decision.decision)} ${dt(po.decision.at)}${po.decision.reason ? ` — ${po.decision.reason}` : ""}`) : null,
      pending ? h("div", { class: "row" }, reason,
        h("button", { class: "primary", onclick: () => {
          const quantities = {}; qtyInputs.forEach((inp, id) => { quantities[id] = intOrNull(inp.value); });
          quiet(act(panel(), () => POST(`/api/purchase-orders/${po.id}/approve`, { quantities, reason: reason.value || null }), (x) => `${x.id} ${label(x.decision.decision)}`).then(refresh));
        } }, "Approve"),
        h("button", { class: "danger", onclick: () => quiet(act(panel(), () => POST(`/api/purchase-orders/${po.id}/reject`, { reason: reason.value }), "Rejected").then(refresh)) }, "Reject")) : null,
      po.status === "APPROVED" ? h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/purchase-orders/${po.id}/received`), "Marked as received").then(refresh)) }, "Mark received") : null));
  }
};

// ------------------------------------------------- suppliers (contacts and price lists)
function supplierForm(s = {}) {
  return h("form", { class: "form" },
    field("Supplier name", input("name", { value: s.name, required: true })),
    field("Contact person", input("contact_name", { value: s.contact_name })),
    field("Phone", input("phone", { value: s.phone })),
    field("Email", input("email", { type: "email", value: s.email })),
    field("Address", input("address", { value: s.address })),
    h("button", { class: "primary", type: "submit" }, s.id ? "Save changes" : "Add supplier"));
}

VIEWS.suppliers = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const canEdit = has("purchasing.decide");
  const suppliers = await GET("/api/suppliers?all=1");
  let products = [];
  try { products = await GET("/api/products"); } catch { /* only needed to add prices */ }
  const defaultCost = new Map(products.map((p) => [p.id, p.unit_cost_cents]));

  add(el, h("h2", {}, "Suppliers"),
    h("p", { class: "muted small" }, "Contact details and the price each supplier charges per unit. A purchase order drafted for a supplier uses its prices; products it does not list keep their default cost."));
  if (!suppliers.length) add(el, h("p", { class: "muted" }, "No suppliers yet."));

  for (const s of suppliers) {
    const priceSel = select("product_id", products.map((p) => [p.id, `${p.name} ${p.strength}`]));
    const priceForm = h("form", { class: "row" }, priceSel,
      input("price", { type: "number", min: "0.01", step: "0.01", required: true, placeholder: "Price per unit ($)", class: "narrow" }),
      h("button", { type: "submit" }, "Set price"));
    priceForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const { product_id, price } = formData(priceForm);
      await quiet(act(panel(), () => POST(`/api/suppliers/${s.id}/prices`, { product_id, unit_cost_cents: centsFromInput(price) }), "Price saved").then(refresh));
    });
    const editForm = supplierForm(s);
    editForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      await quiet(act(panel(), () => PATCH(`/api/suppliers/${s.id}`, formData(editForm)), "Supplier updated").then(refresh));
    });
    add(el, h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", {}, s.name), s.active ? null : badge("Deactivated", "HIGH"), h("span", { class: "muted small mono" }, s.id)),
      kv({ Contact: s.contact_name, Phone: s.phone, Email: s.email, Address: s.address }),
      h("h3", {}, "Price list"),
      table([
        { title: "Product", render: (p) => p.product_name },
        { title: "Supplier price", num: true, render: (p) => money(p.unit_cost_cents) },
        { title: "Default cost", num: true, render: (p) => money(defaultCost.get(p.product_id)) },
        { title: "Updated", render: (p) => dt(p.updated_at) },
        canEdit ? { title: "", render: (p) => h("button", { class: "danger", onclick: () => quiet(act(panel(), () => DEL(`/api/suppliers/${s.id}/prices/${p.product_id}`), "Price removed").then(refresh)) }, "Remove") } : null,
      ].filter(Boolean), s.prices, "No prices listed yet."),
      canEdit && products.length ? priceForm : null,
      canEdit ? h("details", {}, h("summary", {}, "Edit details"), editForm) : null,
      canEdit ? h("div", { class: "row" }, s.active
        ? h("button", { class: "danger", onclick: () => { if (confirm(`Deactivate ${s.name}? Existing orders keep their supplier name.`)) quiet(act(panel(), () => POST(`/api/suppliers/${s.id}/deactivate`), "Deactivated").then(refresh)); } }, "Deactivate")
        : h("button", { onclick: () => quiet(act(panel(), () => POST(`/api/suppliers/${s.id}/reactivate`), "Reactivated").then(refresh)) }, "Reactivate")) : null));
  }

  if (canEdit) {
    const addForm = supplierForm();
    addForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      await quiet(act(panel(), () => POST("/api/suppliers", formData(addForm)), (s) => `Added ${s.name}`).then(refresh));
    });
    add(el, h("h3", {}, "Add supplier"), addForm);
  }
};

// ------------------------------------------------- sales (US8.1)
function receiptView(r) {
  const lines = r.lines.map((l) => `${l.description}\n  ${l.quantity} × ${money(l.unit_price_cents)} = ${money(l.total_cents)}`).join("\n");
  return h("div", { class: "receipt" }, [
    r.pharmacy, `Receipt ${r.receipt_number}`, dt(r.issued_at), `Cashier: ${r.cashier}`, r.patient ? `Patient: ${r.patient}` : null,
    "-".repeat(34), lines, "-".repeat(34),
    `TOTAL      ${money(r.total_cents)}`, `${label(r.method)}  ${money(r.amount_tendered_cents)}`, `Change     ${money(r.change_cents)}`,
  ].filter(Boolean).join("\n"));
}

VIEWS.orders = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const orders = await GET("/api/sales/payable");
  const receipt = state.lastReceipt ? receiptView(state.lastReceipt) : null;
  state.lastReceipt = null;
  add(el, h("h2", {}, "See orders"), h("p", { class: "muted small" }, "Dispensed prescriptions waiting for payment. Payment completes the hand-over."), receipt);
  if (!orders.length) add(el, h("p", { class: "muted" }, "No orders are waiting for payment."));
  for (const o of orders) {
    const method = select("method", ["CARD", "CASH"]);
    const amount = h("input", { type: "number", step: "0.01", min: 0, placeholder: `Cash received (total ${money(o.total_cents)})` });
    add(el, h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, o.prescription_id), h("span", {}, `${o.patient} — ${o.medication} × ${o.quantity}`), h("strong", {}, money(o.total_cents))),
      h("div", { class: "row" }, method, amount,
        h("button", { class: "primary", onclick: () => quiet(act(panel(), () => POST("/api/sales/prescription", {
          prescription_id: o.prescription_id, method: method.value, amount_tendered_cents: method.value === "CASH" ? centsFromInput(amount.value) : null,
        }), (r) => `Paid. Receipt ${r.receipt_number}`).then((r) => { state.lastReceipt = r; refresh(); })) }, "Record payment"))));
  }
};

VIEWS.sales = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const products = (await GET("/api/products")).filter((p) => !p.requires_prescription);
  const lines = h("div");
  const out = h("div");
  function addLine() {
    add(lines, h("div", { class: "row line" }, select("product_id", products.map((p) => [p.id, `${p.name} ${p.strength} (${money(p.unit_price_cents)})`])),
      h("input", { name: "quantity", type: "number", min: 1, value: 1, class: "narrow" }),
      h("button", { type: "button", onclick: (e) => e.target.closest(".line").remove() }, "Remove")));
  }
  addLine();
  const method = select("method", ["CASH", "CARD"]);
  const amount = h("input", { type: "number", step: "0.01", min: 0, placeholder: "Cash received" });
  add(el, h("h2", {}, "Record Sales"), h("p", { class: "muted small" }, "Over-the-counter items. Prescription items are paid from See orders."),
    lines, h("div", { class: "row" }, h("button", { type: "button", onclick: addLine }, "Add item"), method, amount,
      h("button", { class: "primary", onclick: () => {
        const items = [...lines.querySelectorAll(".line")].map((l) => ({ product_id: l.querySelector("select").value, quantity: intOrNull(l.querySelector("input").value) }));
        quiet(act(panel(), () => POST("/api/sales/otc", { items, method: method.value, amount_tendered_cents: method.value === "CASH" ? centsFromInput(amount.value) : null }),
          (r) => `Sale recorded. Receipt ${r.receipt_number}`).then((r) => put(out, receiptView(r))));
      } }, "Record sale")), out);
};

VIEWS.receipts = async (el) => {
  const out = h("div");
  const num = input("number", { placeholder: "R-YYYYMMDD-00001", class: "mono" });
  const sales = await GET("/api/sales");
  const show = async (n) => { try { put(out, receiptView(await GET(`/api/receipts/${encodeURIComponent(n)}`))); } catch (err) { put(out, h("div", { class: "flash error" }, err.message)); } };
  add(el, h("h2", {}, "Issue Receipts"), h("div", { class: "row" }, num, h("button", { onclick: () => show(num.value) }, "Find")), out,
    h("h3", {}, "Recent sales"),
    table([{ title: "Receipt", render: (s) => h("span", { class: "mono" }, s.receipt_number) }, { title: "When", render: (s) => dt(s.created_at) },
      { title: "Linked to", render: (s) => s.prescription_ids.join(", ") || "Over the counter" }, { title: "Method", render: (s) => label(s.method) },
      { title: "Total", num: true, render: (s) => money(s.total_cents) }, { title: "", render: (s) => h("button", { onclick: () => show(s.receipt_number) }, "Show") }],
    sales.slice(0, 50)));
};

// ------------------------------------------------- reports and audit (US8.2)
VIEWS.reports = async (el) => {
  const ops = await GET("/api/reports/operations");
  const today = new Date().toISOString().slice(0, 10);
  const start = input("start", { type: "date", value: `${today.slice(0, 8)}01` });
  const end = input("end", { type: "date", value: today });
  const salesOut = h("div"); const overridesOut = h("div");
  async function load() {
    const q = `?start=${start.value}&end=${end.value}`;
    const [s, o] = await Promise.all([GET(`/api/reports/sales${q}`), GET(`/api/reports/alert-overrides${q}`)]);
    put(salesOut, 
      h("div", { class: "stats" }, [["Transactions", s.transactions], ["Revenue", money(s.total_cents)], ["Prescription sales", s.prescription_sales], ["OTC sales", s.otc_sales],
        ...Object.entries(s.by_method_cents).map(([k, v]) => [label(k), money(v)])].map(([k, v]) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, k)))),
      table([{ title: "Day", render: (r) => r[0] }, { title: "Revenue", num: true, render: (r) => money(r[1]) }], Object.entries(s.by_day_cents), "No sales in this period."),
      h("h3", {}, "Top products"), table([{ title: "Product", render: (r) => r.product }, { title: "Units", num: true, render: (r) => r.units }], s.top_products));
    put(overridesOut, table([
      { title: "When", render: (r) => dt(r.resolved_at) }, { title: "Rx", render: (r) => h("span", { class: "mono" }, r.prescription_id) },
      { title: "Alert", render: (r) => [badge(r.severity), " ", label(r.type)] }, { title: "Rationale", render: (r) => r.rationale }, { title: "Pharmacist", render: (r) => r.resolved_by },
    ], o, "No overrides in this period."));
  }
  [start, end].forEach((i) => i.addEventListener("change", () => quiet(load())));
  add(el, h("h2", {}, "Reports"),
    h("div", { class: "stats" }, [
      ["Avg hours to dispense", ops.average_hours_to_dispense ?? "–"], ["Open clinical alerts", ops.open_clinical_alerts], ["Alerts overridden", ops.alerts_overridden],
      ["At/below reorder point", ops.products_at_or_below_reorder_point], ["Lots expiring ≤ 90 d", ops.lots_expiring_within_90_days],
      ["POs pending approval", ops.purchase_orders_pending], ["Refills open", ops.refills_open], ["Audit entries", ops.audit_entries],
    ].map(([k, v]) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, k)))),
    h("h3", {}, "Prescriptions by stage"),
    table([{ title: "Stage", render: (r) => label(r[0]) }, { title: "Count", num: true, render: (r) => r[1] }], Object.entries(ops.prescriptions_by_status)),
    h("h3", {}, "Sales"), h("div", { class: "row" }, "From", start, "to", end), salesOut,
    h("h3", {}, "Clinical alert overrides"), overridesOut);
  await load();
};

VIEWS.audit = async (el) => {
  const panel = () => el.closest(".main");
  const filters = h("form", { class: "form" },
    field("Action", input("action", { placeholder: "e.g. ALERT_RESOLVED" })), field("Entity type", input("entity_type", { placeholder: "e.g. Prescription" })),
    field("Entity id", input("entity_id")), field("Limit", input("limit", { type: "number", value: 200, min: 1, max: 1000 })),
    h("button", { type: "submit" }, "Filter"));
  const out = h("div");
  async function load() {
    const params = new URLSearchParams(Object.entries(formData(filters)).filter(([, v]) => v));
    const rows = await GET(`/api/audit?${params}`);
    put(out, table([
      { title: "#", num: true, render: (e) => e.sequence }, { title: "Time", render: (e) => dt(e.at) }, { title: "Actor", render: (e) => e.actor_name },
      { title: "Action", render: (e) => h("span", { class: "mono" }, e.action) }, { title: "Entity", render: (e) => `${e.entity_type} ${e.entity_id || ""}` },
      { title: "Details", render: (e) => h("span", { class: "small mono" }, Object.keys(e.details).length ? JSON.stringify(e.details) : "") },
    ], rows, "No matching entries."));
  }
  filters.addEventListener("submit", (e) => { e.preventDefault(); quiet(load()); });
  add(el, h("h2", {}, "Audit log"),
    h("div", { class: "row" }, h("button", { onclick: () => quiet(act(panel(), () => GET("/api/audit/verify"),
      (r) => r.valid ? `Audit chain intact: ${r.checked} entries verified.` : `Audit chain BROKEN at ${r.broken_at}: ${r.reason}`)) }, "Verify integrity"),
    h("span", { class: "muted small" }, "Entries are append-only and hash-chained; any edit or deletion is detected.")),
    filters, out);
  await load();
};

// ------------------------------------------------- patient self-service (US7.1, US7.2)
VIEWS.refills = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const [meds, mine, orders, branches] = await Promise.all([GET("/api/me/refillable"), GET("/api/me/refills"), GET("/api/me/orders"), GET("/api/branches")]);
  const flow = h("div", { class: "phone" });

  function chooseScreen() {
    put(flow, h("h2", {}, "Choose a medication to refill"),
      meds.length ? meds.map((m) => h("button", { class: "tile", disabled: !m.can_request, onclick: () => reviewScreen(m) },
        `${m.medication} ${m.strength || ""} — ${m.prescription_id}`, h("small", {}, m.can_request ? `${m.refills_remaining} refill${m.refills_remaining === 1 ? "" : "s"} remaining · [Request refill]` : m.reason)))
        : h("p", { class: "muted" }, "You have no prescriptions on file."));
  }
  function reviewScreen(m) {
    const branch = select("pickup_branch", branches);
    const note = h("textarea", { name: "note", placeholder: "Note for the pharmacist (optional)", maxlength: 300 });
    put(flow, h("h2", {}, "Review refill request"),
      h("div", { class: "tile" }, `${m.medication} — ${m.prescription_id}`, h("small", {}, `Pickup: ${branch.value}`)),
      field("Pickup branch", branch), note,
      h("button", { class: "tile primary", onclick: () => quiet(act(panel(), () => POST("/api/me/refills", { prescription_id: m.prescription_id, pickup_branch: branch.value, note: note.value || null })).then(submittedScreen)) }, "Submit request"),
      h("button", { class: "tile", onclick: chooseScreen }, "Back"));
    branch.addEventListener("change", () => { flow.querySelector(".tile small").textContent = `Pickup: ${branch.value}`; });
  }
  function submittedScreen(r) {
    put(flow, h("h2", {}, "Request submitted"),
      h("div", { class: "tile" }, `Request ${r.id}`, h("small", {}, r.stage)),
      h("button", { class: "tile", onclick: () => statusScreen(r) }, "Request status"),
      h("button", { class: "tile", onclick: refresh }, "Home"));
  }
  function statusScreen(r) {
    put(flow, h("h2", {}, `Request ${r.id}`), kv({ Medication: r.medication, Status: r.stage, Pickup: r.pickup_branch }),
      h("ul", { class: "timeline" }, r.timeline.map((t) => h("li", {}, `${dt(t.at)} — ${t.stage}`))),
      h("button", { class: "tile", onclick: refresh }, "Home"));
  }
  chooseScreen();

  add(el, flow,
    h("h3", {}, "My refill requests"),
    mine.length ? mine.map((r) => h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", { class: "mono" }, r.id), badge(r.status, r.status === "READY_FOR_PICKUP" ? "LOW" : r.status === "REJECTED" ? "HIGH" : ""), h("span", {}, r.medication)),
      h("p", {}, r.stage, r.rejection_reason ? ` — ${r.rejection_reason}` : ""),
      h("ul", { class: "timeline" }, r.timeline.map((t) => h("li", {}, `${dt(t.at)} — ${t.stage}`))),
      r.can_cancel ? h("button", { class: "danger", onclick: () => quiet(act(panel(), () => POST(`/api/me/refills/${r.id}/cancel`), "Request cancelled").then(refresh)) }, "Cancel request") : null))
      : h("p", { class: "muted" }, "No refill requests yet."),
    h("h3", {}, "Track my orders"),
    orders.length ? orders.map((o) => h("details", { class: "card" },
      h("summary", {}, h("strong", { class: "mono" }, o.prescription_id), ` ${o.medication} — ${o.stage}`),
      h("ul", { class: "timeline" }, o.timeline.map((t) => h("li", {}, `${dt(t.at)} — ${t.stage}`)))))
      : h("p", { class: "muted" }, "No orders."));
};

VIEWS.notifications = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const [{ preferences: p }, sent] = await Promise.all([GET("/api/me/notification-preferences"), GET("/api/me/notifications")]);
  const choice = select("channel", [["SMS", "SMS text message"], ["EMAIL", "Email"], ["IN_APP", "In-app only"]], { value: p.channel || "SMS" });
  add(el, h("h2", {}, "Select notifications"),
    kv({ "Current channel": p.channel ? label(p.channel) : "Not chosen", Consent: p.consent_given ? `Given (policy ${p.policy_version})` : "Not given – no messages are sent" }),
    h("div", { class: "row" }, choice, h("button", { class: "primary", onclick: () => quiet(act(panel(), () => POST("/api/me/notification-preferences/channel", { channel: choice.value }), "Channel saved").then(refresh)) }, "Save channel")),
    !p.consent_given ? h("p", { class: "small muted" }, "Messages are only sent after you give consent under Consent and Policies.") : null,
    h("h3", {}, "Messages"),
    table([{ title: "When", render: (n) => dt(n.created_at) }, { title: "Subject", render: (n) => n.subject }, { title: "Message", render: (n) => n.message },
      { title: "Delivery", render: (n) => n.status === "SENT" ? `${label(n.channel)} → ${n.destination}` : h("span", { class: "muted" }, `Not sent: ${n.suppressed_reason}`) }], sent, "No messages yet."));
};

VIEWS.consent = async (el, refresh) => {
  const panel = () => el.closest(".main");
  const { preferences: p, policy } = await GET("/api/me/notification-preferences");
  add(el, h("h2", {}, "Consent and Policies"),
    h("div", { class: "card" }, h("h3", {}, `Communication policy (version ${policy.version})`), h("p", {}, policy.text)),
    kv({ Status: p.consent_given ? `Consent given for ${label(p.channel)}` : "No consent on file", Channel: p.channel ? label(p.channel) : "Choose one under Select notifications first" }),
    h("div", { class: "row" },
      p.consent_given
        ? h("button", { class: "danger", onclick: () => quiet(act(panel(), () => POST("/api/me/consent/withdraw"), "Consent withdrawn").then(refresh)) }, "Withdraw consent")
        : h("button", { class: "primary", disabled: !p.channel, onclick: () => quiet(act(panel(), () => POST("/api/me/consent", { policy_version: policy.version }), "Consent recorded").then(refresh)) }, "I agree – give consent")),
    h("h3", {}, "History"),
    table([{ title: "When", render: (e) => dt(e.at) }, { title: "Action", render: (e) => label(e.action) }, { title: "Channel", render: (e) => e.channel ? label(e.channel) : "" },
      { title: "Policy", render: (e) => e.policy_version || "" }], [...p.history].reverse(), "No consent history yet."));
};

boot();
