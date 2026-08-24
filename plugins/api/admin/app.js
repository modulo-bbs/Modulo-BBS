/* Modulo BBS SysOp Console — vanilla JS. No frameworks, no build step.
 * Talks exclusively to the One-API (/api/v1/...). */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------------------------------------------------------- state
let token = sessionStorage.getItem("modulo-token") || "";
let me = null;
let pollTimer = null;
let usersPage = 1;
const usersPerPage = 25;

// ---------------------------------------------------------------- api
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(op, params = {}) {
  const res = await fetch(`/api/v1/${op}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(params),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 403 && token) { doLogout(true); }
    throw new ApiError(body.error || res.statusText, res.status);
  }
  return body;
}

// ---------------------------------------------------------------- auth
function showConsole() {
  $("#login-view").classList.add("hidden");
  $("#console-view").classList.remove("hidden");
  refreshAll();
  clearInterval(pollTimer);
  pollTimer = setInterval(refreshLive, 3000);
}

function showLogin() {
  clearInterval(pollTimer);
  $("#console-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  $("#login-user").focus();
}

function doLogout(expired) {
  if (token) { api("auth.logout", { token }).catch(() => {}); }
  token = "";
  me = null;
  sessionStorage.removeItem("modulo-token");
  showLogin();
  if (expired) {
    $("#login-error").textContent = "Session expired — sign in again.";
    $("#login-error").classList.remove("hidden");
  }
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const errEl = $("#login-error");
  errEl.classList.add("hidden");
  try {
    const r = await api("auth.login", {
      username: $("#login-user").value.trim(),
      password: $("#login-pass").value,
    });
    token = r.token;
    me = r.user;
    sessionStorage.setItem("modulo-token", token);
    if (!me.groups.includes("sysop")) {
      throw new ApiError("This console requires sysop access.", 403);
    }
    showConsole();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
});

$("#logout").addEventListener("click", () => doLogout(false));

// ---------------------------------------------------------------- tabs
$$("button.tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$("button.tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$("main > section").forEach((s) => s.classList.add("hidden"));
    $(`#tab-${btn.dataset.tab}`).classList.remove("hidden");
    refreshAll();
  })
);

// ---------------------------------------------------------------- dashboard
async function refreshHealth() {
  try {
    const h = await api("system.health");
    $("#stat-nodes").textContent = h.nodes.active;
    $("#stat-max").textContent = h.nodes.max;
    $("#stat-plugins").textContent = h.plugins.length;
    $("#health-json").textContent = JSON.stringify(h, null, 2);
    $("#conn-status").textContent = "connected";
    $("#conn-status").style.color = "var(--green)";
  } catch {
    $("#conn-status").textContent = "connection lost";
    $("#conn-status").style.color = "var(--red)";
  }
}

async function refreshSessions() {
  const body = $("#sessions-body");
  try {
    const r = await api("sessions.list");
    if (!r.sessions.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No active sessions.</td></tr>`;
      return;
    }
    body.innerHTML = r.sessions
      .map(
        (s) => `<tr>
          <td>${s.node}</td>
          <td>${esc(s.username)}</td>
          <td>${esc(s.address)}</td>
          <td><span class="badge">${esc(s.state)}</span></td>
          <td>${esc(s.idle)}</td>
          <td class="actions"><button class="danger" data-kick="${esc(s.session_id)}">Kick</button></td>
        </tr>`
      )
      .join("");
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="error">${esc(e.message)}</td></tr>`;
  }
}

$("#sessions-body").addEventListener("click", async (ev) => {
  const sid = ev.target?.dataset?.kick;
  if (!sid) return;
  if (!confirm(`Kick session ${sid}?`)) return;
  try {
    await api("sessions.kick", { session_id: sid });
    refreshSessions();
  } catch (e) {
    alert(e.message);
  }
});

// ---------------------------------------------------------------- users
async function refreshUsers() {
  const body = $("#users-body");
  const filter = ($("#user-filter").value || "").toLowerCase();
  try {
    const r = await api("users.list", { page: usersPage, per_page: usersPerPage });
    const all = r.users.filter(
      (u) => !filter || u.username.includes(filter) || (u.display_name || "").toLowerCase().includes(filter)
    );
    $("#users-count").textContent =
      `${r.total} account${r.total === 1 ? "" : "s"}` +
      (r.pages > 1 ? ` · page ${r.page}/${r.pages}` : "");
    $("#users-prev").disabled = r.page <= 1;
    $("#users-next").disabled = r.page >= r.pages;
    if (!all.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No users match.</td></tr>`;
      return;
    }
    body.innerHTML = all
      .map(
        (u) => `<tr>
          <td><strong>${esc(u.username)}</strong></td>
          <td>${esc(u.display_name || "")}</td>
          <td>${u.groups.map((g) => `<span class="badge ${g}">${esc(g)}</span>`).join("")}</td>
          <td>${(u.created || "").slice(0, 10)}</td>
          <td>${u.last_login ? esc(u.last_login.slice(0, 16).replace("T", " ")) : "—"}</td>
          <td class="actions">
            <button data-edit="${esc(u.username)}">Edit</button>
            <button class="danger" data-del="${esc(u.username)}">Delete</button>
          </td>
        </tr>`
      )
      .join("");
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="error">${esc(e.message)}</td></tr>`;
  }
}

function userDialogFor(existing) {
  $("#user-dialog-title").textContent = existing ? `Edit: ${existing.username}` : "New user";
  $("#uf-username").value = existing?.username || "";
  $("#uf-username").disabled = !!existing; // immutable PK
  $("#uf-password").value = "";
  $("#uf-display").value = existing?.display_name || "";
  $("#uf-email").value = existing?.email || "";
  $("#uf-groups").value = existing?.groups.join(",") || "user";
  $("#uf-save").dataset.mode = existing ? "edit" : "create";
  $("#user-dialog-error").classList.add("hidden");
  $("#user-dialog").showModal();
}

$("#user-new").addEventListener("click", () => userDialogFor(null));
$("#users-body").addEventListener("click", (ev) => {
  const edit = ev.target?.dataset?.edit;
  const del = ev.target?.dataset?.del;
  if (edit) {
    api("users.get", { username: edit })
      .then((u) => userDialogFor(u))
      .catch((e) => alert(e.message));
  }
  if (del) {
    if (confirm(`Delete account "${del}"? Authored content keeps its author name.`)) {
      api("users.delete", { username: del })
        .then(refreshUsers)
        .catch((e) => alert(e.message));
    }
  }
});
$("#user-filter").addEventListener("input", () => { usersPage = 1; refreshUsers(); });
$("#users-prev").addEventListener("click", () => { if (usersPage > 1) { usersPage--; refreshUsers(); } });
$("#users-next").addEventListener("click", () => { usersPage++; refreshUsers(); });

$("#user-form").addEventListener("submit", async (ev) => {
  if (ev.submitter?.value !== "save") return;
  ev.preventDefault();
  const mode = $("#uf-save").dataset.mode;
  const username = $("#uf-username").value.trim();
  const errEl = $("#user-dialog-error");
  try {
    if (mode === "create") {
      await api("users.create", {
        username,
        password: $("#uf-password").value,
        display_name: $("#uf-display").value,
        email: $("#uf-email").value,
        groups: $("#uf-groups").value,
      });
    } else {
      await api("users.update", {
        username,
        password: $("#uf-password").value,
        display_name: $("#uf-display").value,
        email: $("#uf-email").value,
        groups: $("#uf-groups").value,
      });
    }
    $("#user-dialog").close();
    refreshUsers();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
});

// ---------------------------------------------------------------- API explorer
async function loadSchema() {
  const res = await fetch("/api/v1/_schema", {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const schema = await res.json();
  const list = $("#op-list");
  list.innerHTML = "";
  for (const op of schema.operations) {
    const row = document.createElement("div");
    row.className = "op-row";
    row.innerHTML = `
      <span class="op-name">${esc(op.name)}</span>
      <span class="muted">${esc(op.description || "")}</span>
      <button data-op="${esc(op.name)}">Try</button>`;
    list.appendChild(row);
  }
}

$("#op-list").addEventListener("click", (ev) => {
  const name = ev.target?.dataset?.op;
  if (!name) return;
  openOpDialog(name);
});

function openOpDialog(name) {
  fetch("/api/v1/_schema")
    .then((r) => r.json())
    .then((schema) => {
      const op = schema.operations.find((o) => o.name === name);
      if (!op) return;
      $("#op-title").textContent = op.name;
      $("#op-desc").textContent = op.description || "";
      $("#op-result").classList.add("hidden");
      const fields = $("#op-fields");
      fields.innerHTML = "";
      for (const [p, t] of Object.entries(op.params || {})) {
        fields.insertAdjacentHTML(
          "beforeend",
          `<label>${p} <small>(${t}, required)</small>
             <input type="text" data-param="${esc(p)}" required></label>`
        );
      }
      for (const [p, spec] of Object.entries(op.optional || {})) {
        fields.insertAdjacentHTML(
          "beforeend",
          `<label>${p} <small>(${spec.type}, default ${JSON.stringify(spec.default)})</small>
             <input type="text" data-param="${esc(p)}" data-optional="1"
                    placeholder="${esc(String(spec.default))}"></label>`
        );
      }
      if (!(op.params && Object.keys(op.params).length)) {
        fields.insertAdjacentHTML("beforeend",
          `<p class="muted">No required parameters.</p>`);
      }
      $("#op-run").onclick = async (ev) => {
        ev.preventDefault();
        const params = {};
        $$("#op-fields input").forEach((inp) => {
          const v = inp.value.trim();
          if (!v && inp.dataset.optional !== undefined) return;
          params[inp.dataset.param] = coerce(v, inp.placeholder);
        });
        try {
          const result = await api(name, params);
          $("#op-result").textContent = JSON.stringify(result, null, 2);
          $("#op-result").classList.remove("hidden");
        } catch (e) {
          $("#op-result").textContent = "ERROR: " + e.message;
          $("#op-result").classList.remove("hidden");
        }
      };
      $("#op-dialog").showModal();
    });
}

// ---------------------------------------------------------------- helpers
function coerce(value, placeholder) {
  // Numbers when they look like numbers, booleans for true/false, else string.
  if (value === "true") return true;
  if (value === "false") return false;
  if (value !== "" && !isNaN(Number(value)) && placeholder !== undefined &&
      /^[\d.]+$/.test(placeholder) ) return Number(value);
  return value;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ---------------------------------------------------------------- boot
function refreshAll() {
  refreshHealth();
  refreshUsers();
  refreshSessions();
  loadSchema().catch(() => {});
}
function refreshLive() {
  refreshHealth();
  refreshSessions();
}

(async function boot() {
  if (token) {
    try {
      // Validate stored token with a cheap authenticated call.
      const health = await api("system.health");
      void health;
      showConsole();
      return;
    } catch { /* fall through to login */ }
  }
  showLogin();
})();
