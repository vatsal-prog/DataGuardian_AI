const $ = (selector) => document.querySelector(selector);

const state = { busy: false };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || response.statusText);
  }
  return body;
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("button").forEach((button) => {
    if (button.dataset.view) return;
    button.disabled = busy;
  });
}

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    view.hidden = view.id !== `view-${name}`;
  });
  document.querySelectorAll(".views button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  if (name === "data") loadDatasets();
  if (name === "ask") loadDatasetOptions();
  if (name === "reviews") loadReviews();
}

document.querySelectorAll(".views button").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

async function refreshMeta() {
  const [health, reviews] = await Promise.all([api("/api/health"), api("/api/reviews")]);
  const label = health.warehouse === "postgresql" ? "PostgreSQL" : "SQLite";
  $("#warehouse").textContent = `Warehouse: ${label} · ${health.datasets} published`;
  const pending = reviews.filter((review) => review.status === "pending").length;
  $("#review-count").textContent = pending ? `(${pending})` : "";
}

async function run(path, options) {
  $("#run-error").textContent = "";
  setBusy(true);
  $("#report").hidden = false;
  $("#report").innerHTML = "<p class='lede'>Running ingestion, profiling, quality, root cause, repair, and validation.</p>";
  $("#empty-arch").hidden = true;
  try {
    const report = await api(path, options);
    renderReport(report);
    await refreshMeta();
  } catch (error) {
    $("#run-error").textContent = error.message;
    $("#report").hidden = true;
    $("#empty-arch").hidden = false;
  } finally {
    setBusy(false);
  }
}

document.querySelectorAll("[data-sample]").forEach((button) => {
  button.addEventListener("click", () => run(`/api/pipeline/samples/${button.dataset.sample}`, { method: "POST" }));
});

$("#upload-form").addEventListener("change", () => {
  const file = $("#file").files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  run("/api/pipeline/upload", { method: "POST", body });
  $("#upload-form").reset();
});

const drop = $(".drop");
["dragover", "dragenter"].forEach((eventName) => {
  drop.addEventListener(eventName, (event) => {
    event.preventDefault();
    drop.classList.add("hot");
  });
});
drop.addEventListener("dragleave", () => drop.classList.remove("hot"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("hot");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  run("/api/pipeline/upload", { method: "POST", body });
});

$("#api-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "api", url: $("#api-url").value }),
  });
});

$("#demo-api").addEventListener("click", () => {
  const url = `${window.location.origin}/api/demo/feed`;
  $("#api-url").value = url;
  run("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "api", url }),
  });
});

$("#db-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const kind = $("#db-kind").value;
  const location = $("#db-url").value;
  run("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind,
      url: kind === "postgres" ? location : undefined,
      path: kind === "sqlite" ? location : undefined,
      query: $("#db-query").value,
    }),
  });
});

function renderReport(report) {
  const passed = report.status === "pass";
  const validation = report.validation || {};
  $("#report").hidden = false;
  $("#empty-arch").hidden = true;
  $("#report").innerHTML = `
    <header class="card" style="border-top:0;padding-top:0">
      <div>
        <span class="pill ${passed ? "pass" : "fail"}">${esc(report.status)}</span>
        <h2 style="margin-top:0.4rem">${esc(report.display_name)}</h2>
        <p class="lede">${esc(report.decision_detail)}</p>
      </div>
    </header>
    <div class="metrics">
      ${metric("Rows in", report.rows_before)}
      ${metric("Rows out", report.rows_after)}
      ${metric("Score before", validation.score_before)}
      ${metric("Score after", validation.score_after)}
    </div>
    ${rail(passed)}
    ${ingestion(report.ingestion)}
    ${profile(report.profile)}
    ${issues(report.issues, "Issues found")}
    ${causes(report.root_causes)}
    ${repairs(report.repairs)}
    ${checks(validation)}
    ${issues(validation.residual_issues || [], "Remaining after automatic repair")}
    <div class="cols">
      <div><h3>Before</h3>${table(report.preview_before)}</div>
      <div><h3>After automatic repair</h3>${table(report.preview_after)}</div>
    </div>
  `;
}

function metric(label, value) {
  return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}

function rail(passed) {
  const stages = [
    ["Data sources", "CSV, JSON, APIs, PostgreSQL"],
    ["Ingestion agent", "Schema understood"],
    ["Data profiler", "Types and distributions"],
    ["Quality agent", "Issues listed"],
    ["Root-cause agent", "Causes attached"],
    ["Repair agent", "Safe fixes applied"],
    ["Validation agent", passed ? "Passed" : "Failed"],
  ];
  return `
    <ol class="rail">
      ${stages.map(([title, detail], index) => `<li class="${index === stages.length - 1 && !passed ? "fail" : "done"}"><strong>${title}</strong><span>${detail}</span></li>`).join("")}
    </ol>
    <div class="branch">
      <div class="${passed ? "on" : "off"}"><strong>Pass · store</strong><span>Rows are in the warehouse. Ask a question or open Datasets.</span></div>
      <div class="${passed ? "off" : "on"}"><strong>Fail · rollback</strong><span>Nothing was published. Open Review to approve or discard.</span></div>
    </div>
  `;
}

function ingestion(block) {
  if (!block) return "";
  const notes = (block.notes || []).map((note) => `<li>${esc(note)}</li>`).join("");
  return `<h3>Ingestion</h3><ul>${notes}</ul>${table(block.schema, ["name", "original_name", "source_dtype", "examples"])}`;
}

function profile(block) {
  if (!block) return "";
  const cards = (block.columns || []).map((column) => {
    const bars = column.histogram?.length ? histogram(column.histogram) : topBars(column.top_values || []);
    const stats = column.stats
      ? `<p class="lede">min ${esc(column.stats.min)} · mean ${esc(column.stats.mean)} · median ${esc(column.stats.median)} · max ${esc(column.stats.max)}</p>`
      : "";
    return `<article class="card"><header><strong>${esc(column.name)}</strong><span class="pill info">${esc(column.inferred_type)}</span></header><p class="lede">${esc(column.null_count)} empty · ${esc(column.sentinel_count)} sentinels · ${esc(column.unique_count)} distinct</p>${stats}${bars}</article>`;
  }).join("");
  return `<h3>Profile</h3><p class="lede">${esc(block.row_count)} rows · ${esc(block.duplicate_row_count)} extra duplicate rows</p>${cards}`;
}

function histogram(buckets) {
  const max = Math.max(...buckets.map((bucket) => bucket.count), 1);
  return `<div class="bars">${buckets.map((bucket) => `<div class="bar"><span>${esc(bucket.lo)}–${esc(bucket.hi)}</span><i><span style="width:${(bucket.count / max) * 100}%"></span></i><span>${esc(bucket.count)}</span></div>`).join("")}</div>`;
}

function topBars(values) {
  if (!values.length) return "";
  const max = Math.max(...values.map((item) => item.count), 1);
  return `<div class="bars">${values.map((item) => `<div class="bar"><span>${esc(item.value)}</span><i><span style="width:${(item.count / max) * 100}%"></span></i><span>${esc(item.count)}</span></div>`).join("")}</div>`;
}

function issues(rows, title) {
  if (!rows) return "";
  if (!rows.length) return `<h3>${esc(title)}</h3><p class="empty">None.</p>`;
  const body = rows.map((issue) => `<tr><td><span class="pill ${esc(issue.severity)}">${esc(issue.severity)}</span></td><td>${esc(issue.issue_type)}</td><td>${esc(issue.column || "—")}</td><td>${esc(issue.row_count)}</td><td>${esc(issue.description)}</td></tr>`).join("");
  return `<h3>${esc(title)}</h3><table><thead><tr><th>Severity</th><th>Type</th><th>Column</th><th>Rows</th><th>What is wrong</th></tr></thead><tbody>${body}</tbody></table>`;
}

function causes(rows) {
  if (!rows?.length) return "";
  const cards = rows.map((cause) => `<article class="card"><header><strong>${esc(cause.column || "dataset")}</strong><span class="pill info">${esc(cause.category)} · ${Math.round(cause.confidence * 100)}%</span></header><p>${esc(cause.summary)}</p></article>`).join("");
  return `<h3>Root cause</h3>${cards}`;
}

function repairs(rows) {
  if (!rows?.length) return "";
  const body = rows.map((repair) => `<tr><td>${esc(repair.strategy)}</td><td>${esc(repair.column || "—")}</td><td>${esc(labelRepair(repair))}</td><td>${esc(repair.rows_affected || 0)}</td><td>${esc(repair.description)}</td></tr>`).join("");
  return `<h3>Repairs</h3><table><thead><tr><th>Strategy</th><th>Column</th><th>Disposition</th><th>Rows</th><th>What changes</th></tr></thead><tbody>${body}</tbody></table>`;
}

function labelRepair(repair) {
  if (repair.applied && repair.rows_affected) {
    return repair.disposition === "review" ? "Applied on approval" : "Applied";
  }
  if (repair.disposition === "review") return "Held for review";
  if (repair.disposition === "suggested") return "Suggested only";
  return "Not needed";
}

function checks(validation) {
  const rows = validation.checks || [];
  if (!rows.length) return "";
  const body = rows.map((check) => `<tr><td><span class="pill ${check.passed ? "pass" : "fail"}">${check.passed ? "pass" : "fail"}</span></td><td>${esc(check.name)}</td><td>${esc(check.detail)}</td></tr>`).join("");
  return `<h3>Validation</h3><table><thead><tr><th></th><th>Check</th><th>Detail</th></tr></thead><tbody>${body}</tbody></table>`;
}

function table(rows, keys) {
  if (!rows?.length) return "<p class='empty'>No rows.</p>";
  const columns = keys || Object.keys(rows[0]);
  const head = columns.map((key) => `<th>${esc(key)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((key) => `<td>${esc(formatCell(row[key]))}</td>`).join("")}</tr>`).join("");
  return `<div style="overflow-x:auto"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function formatCell(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value ?? "";
}

async function loadDatasets() {
  const datasets = await api("/api/datasets");
  if (!datasets.length) {
    $("#datasets").innerHTML = "<p class='empty'>Nothing published yet. A pipeline run has to pass first.</p>";
    return;
  }
  $("#datasets").innerHTML = datasets.map((dataset) => `
    <article class="card" data-dataset="${esc(dataset.id)}">
      <header>
        <strong>${esc(dataset.display_name)}</strong>
        <span class="pill pass">score ${esc(dataset.quality_score)}</span>
      </header>
      <p class="lede">${esc(dataset.row_count)} rows · ${esc(dataset.column_count)} columns · ${esc(dataset.source_kind)} · ${esc(dataset.created_at)}</p>
      <button type="button" data-preview="${esc(dataset.id)}" class="ghost">Preview</button>
      <div class="preview"></div>
    </article>
  `).join("");
  $("#datasets").querySelectorAll("[data-preview]").forEach((button) => {
    button.addEventListener("click", async () => {
      const detail = await api(`/api/datasets/${button.dataset.preview}?limit=12`);
      button.parentElement.querySelector(".preview").innerHTML = table(detail.rows);
    });
  });
}

async function loadDatasetOptions() {
  const datasets = await api("/api/datasets");
  const select = $("#dataset");
  select.innerHTML = datasets.length
    ? datasets.map((dataset) => `<option value="${esc(dataset.id)}">${esc(dataset.display_name)} · ${esc(dataset.row_count)} rows</option>`).join("")
    : "<option value=''>No published datasets</option>";
}

$("#ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#ask-error").textContent = "";
  $("#answer").innerHTML = "";
  try {
    const result = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: $("#question").value,
        dataset_id: $("#dataset").value || null,
      }),
    });
    $("#answer").innerHTML = `
      <p class="answer">${esc(result.answer)}</p>
      ${result.rationale ? `<p class="lede">${esc(result.rationale)}</p>` : ""}
      ${result.sql ? `<pre class="sql">${esc(result.sql)}</pre>` : ""}
      ${table(result.rows)}
    `;
  } catch (error) {
    $("#ask-error").textContent = error.message;
  }
});

async function loadReviews() {
  const reviews = await api("/api/reviews");
  if (!reviews.length) {
    $("#reviews").innerHTML = "<p class='empty'>No reviews. A failed validation opens one.</p>";
    return;
  }
  $("#reviews").innerHTML = reviews.map((review) => `
    <article class="card">
      <header>
        <strong>${esc(review.display_name)}</strong>
        <span class="pill ${review.status === "pending" ? "warn" : review.status === "approved" ? "pass" : "fail"}">${esc(review.status)}</span>
      </header>
      <p>${esc(review.decision_detail || "")}</p>
      <p class="lede">Score after automatic repair: ${esc(review.score_after)}. ${esc((review.residual_issues || []).length)} issue(s) still open.</p>
      ${repairs(review.repairs || [])}
      ${review.status === "pending" ? `<div class="row"><button type="button" data-approve="${esc(review.id)}">Approve repairs</button><button type="button" class="ghost" data-reject="${esc(review.id)}">Discard</button></div>` : ""}
      <div class="preview"></div>
    </article>
  `).join("");
  $("#reviews").querySelectorAll("[data-approve]").forEach((button) => {
    button.addEventListener("click", () => decide(button, "approve"));
  });
  $("#reviews").querySelectorAll("[data-reject]").forEach((button) => {
    button.addEventListener("click", () => decide(button, "reject"));
  });
}

async function decide(button, action) {
  const id = button.dataset.approve || button.dataset.reject;
  const card = button.closest(".card");
  try {
    const result = await api(`/api/reviews/${id}/${action}`, { method: "POST" });
    card.querySelector(".preview").innerHTML = `<p class="answer">${esc(result.decision_detail || result.status)}</p>`;
    await refreshMeta();
    await loadReviews();
  } catch (error) {
    card.querySelector(".preview").innerHTML = `<p class="hint">${esc(error.message)}</p>`;
  }
}

refreshMeta().catch((error) => {
  $("#warehouse").textContent = error.message;
});
