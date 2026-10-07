/* Event Sync Service frontend.
 *
 * Deliberately dependency-free: the assessment does not reward build tooling,
 * and a single static file keeps "one command to start" true end to end.
 */

const state = { meetings: [], selected: null };

const els = {
  stats: document.getElementById("stats"),
  tbody: document.querySelector("#meetings tbody"),
  empty: document.getElementById("empty"),
  detail: document.getElementById("detail"),
  detailBody: document.getElementById("detail-body"),
  source: document.getElementById("filter-source"),
  conflicts: document.getElementById("filter-conflicts"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
  reset: document.getElementById("reset"),
  close: document.getElementById("close-detail"),
};

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function fmtWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function sourceBadges(sources) {
  return sources.map((s) => `<span class="badge ${s}">${s}</span>`).join("");
}

function conflictNames(m) {
  return Object.entries(m.fields)
    .filter(([, f]) => f.conflict)
    .map(([name]) => name);
}

function renderStats(stats) {
  const items = [
    ["Total meetings", stats.total_meetings],
    ["Matched", stats.matched],
    ["CRM only", stats.crm_only],
    ["Calendar only", stats.calendar_only],
    ["With conflicts", stats.meetings_with_conflicts],
    ["Conflicts", stats.total_conflicts],
    ["Possible duplicates", stats.possible_duplicates],
    ["Quality flags", stats.records_with_quality_flags],
  ];
  els.stats.innerHTML = items
    .map(([label, value]) => `<div class="stat"><div class="value">${value}</div><div class="label">${label}</div></div>`)
    .join("");
}

function renderTable() {
  const rows = state.meetings;
  els.empty.hidden = rows.length > 0;
  els.tbody.innerHTML = rows.map((m) => {
    const conflicts = conflictNames(m);
    const lowConf = m.match_confidence < 0.7 && m.sources.length > 1;
    const flags = [];
    if (conflicts.length) flags.push(`<span class="badge conflict">${conflicts.length} conflict${conflicts.length > 1 ? "s" : ""}</span>`);
    if (m.possible_duplicate_of) flags.push(`<span class="badge flag">possible duplicate</span>`);
    if (m.data_quality_flags.length) flags.push(`<span class="badge flag">${m.data_quality_flags.length} data issue${m.data_quality_flags.length > 1 ? "s" : ""}</span>`);
    if (m.sources.length === 1) flags.push(`<span class="badge flag">single source</span>`);

    return `<tr data-id="${m.id}">
      <td>${escapeHtml(m.title || "(untitled)")}</td>
      <td>${fmtWhen(m.start_time)}</td>
      <td>${sourceBadges(m.sources)}</td>
      <td>${m.sources.length > 1 ? `<span class="badge ${lowConf ? "low" : ""}">${m.match_confidence.toFixed(2)}</span>` : "—"}</td>
      <td>${flags.join(" ")}</td>
    </tr>`;
  }).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderValue(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? escapeHtml(value.join(", ")) : "—";
  if (typeof value === "object") return escapeHtml(JSON.stringify(value));
  return escapeHtml(value);
}

function renderDetail(m) {
  const notices = [];
  if (m.possible_duplicate_of) {
    notices.push(`Flagged as a possible duplicate of <strong>${escapeHtml(m.possible_duplicate_of)}</strong> in the same source. Not auto-merged — needs a human decision.`);
  }
  if (m.data_quality_flags.length) {
    notices.push(`Data quality: ${m.data_quality_flags.map((f) => escapeHtml(f)).join(", ")}`);
  }

  const fieldRows = Object.entries(m.fields).map(([name, field]) => {
    const candidates = field.candidates.map((c) => {
      const losing = field.conflict && c.value !== field.value;
      return `<div class="candidate ${losing ? "losing" : ""}">
        <span class="src"><span class="badge ${c.source}">${c.source}</span></span>
        <span>${renderValue(c.value)}</span>
      </div>`;
    }).join("");
    return `<div class="field ${field.conflict ? "conflict" : ""}">
      <div class="name">${escapeHtml(name)}${field.conflict ? " — conflict" : ""}</div>
      <div class="chosen">${renderValue(field.value)}</div>
      ${field.candidates.length > 1 ? `<div class="candidates">${candidates}</div>` : ""}
    </div>`;
  }).join("");

  els.detailBody.innerHTML = `
    <h2>${escapeHtml(m.title || "(untitled)")}</h2>
    <div class="meta">
      ${m.sources.map((s) => `<span class="badge ${s}">${s}</span> ${escapeHtml(m.source_ids[s])}`).join(" &nbsp; ")}
      ${m.sources.length > 1 ? ` &nbsp;·&nbsp; match confidence ${m.match_confidence.toFixed(2)}` : ""}
    </div>
    ${notices.map((n) => `<div class="notice">${n}</div>`).join("")}
    <div class="reasons">
      <div class="name">Match signals</div>
      ${m.match_reasons.map((r) => `<code>${escapeHtml(r)}</code>`).join("")}
    </div>
    ${fieldRows}
  `;
  els.detail.hidden = false;
}

function applyFilters() {
  const source = els.source.value;
  const conflictsOnly = els.conflicts.checked;
  const from = els.from.value;
  const to = els.to.value;

  state.meetings = state.all.filter((m) => {
    if (source && !m.sources.includes(source)) return false;
    if (conflictsOnly && !conflictNames(m).length) return false;
    if (from && (!m.start_time || m.start_time.slice(0, 10) < from)) return false;
    if (to && (!m.start_time || m.start_time.slice(0, 10) > to)) return false;
    return true;
  });
  renderTable();
}

async function load() {
  const [stats, meetings] = await Promise.all([
    api("/api/stats"),
    api("/api/meetings"),
  ]);
  renderStats(stats);
  state.all = meetings;
  applyFilters();
}

els.tbody.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  const meeting = state.all.find((m) => m.id === row.dataset.id);
  if (meeting) renderDetail(meeting);
});

els.close.addEventListener("click", () => { els.detail.hidden = true; });
els.source.addEventListener("change", applyFilters);
els.conflicts.addEventListener("change", applyFilters);
els.from.addEventListener("change", applyFilters);
els.to.addEventListener("change", applyFilters);
els.reset.addEventListener("click", () => {
  els.source.value = "";
  els.conflicts.checked = false;
  els.from.value = "";
  els.to.value = "";
  applyFilters();
});

load().catch((err) => {
  els.empty.hidden = false;
  els.empty.textContent = `Failed to load: ${err.message}`;
});
