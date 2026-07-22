const state = { templates: [], inbox: [], jobs: [], timer: null };
const byId = (id) => document.getElementById(id);

function token() { return sessionStorage.getItem("scopingApiToken") || ""; }

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function setConnection(online, label) {
  byId("connection-dot").classList.toggle("online", online);
  byId("connection-label").textContent = label;
}

function showMessage(message, isError = false) {
  const element = byId("message");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  window.setTimeout(() => element.classList.add("hidden"), 5000);
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function configureModes(templateSelect, modeSelect) {
  const template = state.templates.find((item) => item.id === templateSelect.value);
  modeSelect.replaceChildren();
  for (const mode of template?.modes || []) {
    const option = document.createElement("option");
    option.value = mode.id;
    option.textContent = mode.label;
    modeSelect.append(option);
  }
}

function renderInbox() {
  const container = byId("inbox");
  container.replaceChildren();
  const active = state.inbox.filter((item) => item.status !== "dismissed");
  if (!active.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No routed meetings are waiting. New OneNote deliveries will appear here.";
    container.append(empty);
    return;
  }

  for (const item of active) {
    const card = byId("inbox-card-template").content.firstElementChild.cloneNode(true);
    card.querySelector(".recording-id").textContent = `Recording ${item.recording_id}`;
    card.querySelector(".inbox-status").textContent = item.status;
    card.querySelector(".recording-title").textContent = item.recording_title;
    card.querySelector(".routed-time").textContent = `Routed ${formatDate(item.created_at)}`;

    const templateSelect = card.querySelector(".template-select");
    const modeSelect = card.querySelector(".mode-select");
    for (const template of state.templates) {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = template.name;
      templateSelect.append(option);
    }
    configureModes(templateSelect, modeSelect);
    templateSelect.addEventListener("change", () => configureModes(templateSelect, modeSelect));

    const oneNoteLink = card.querySelector(".onenote-link");
    if (item.onenote_link) {
      oneNoteLink.href = item.onenote_link;
      oneNoteLink.classList.remove("hidden");
    }

    const startButton = card.querySelector(".start-button");
    startButton.addEventListener("click", async () => {
      startButton.disabled = true;
      startButton.textContent = "Starting...";
      try {
        await api("/api/scoping/jobs", {
          method: "POST",
          body: JSON.stringify({
            recording_id: item.recording_id,
            template_id: templateSelect.value,
            mode: modeSelect.value,
            start_extraction: true,
          }),
        });
        showMessage(`Extraction started for ${item.recording_title}.`);
        await refresh();
      } catch (error) {
        handleError(error);
      } finally {
        startButton.disabled = false;
        startButton.textContent = "Extract answers";
      }
    });

    card.querySelector(".dismiss-button").addEventListener("click", async () => {
      try {
        await api(`/api/scoping/inbox/${item.recording_id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "dismissed" }),
        });
        await refresh();
      } catch (error) { handleError(error); }
    });
    container.append(card);
  }
}

function actionButton(label, handler, secondary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = secondary ? "button secondary" : "button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function renderJobs() {
  const container = byId("jobs");
  container.replaceChildren();
  if (!state.jobs.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Select a routed meeting above to start its first document job.";
    container.append(empty);
    return;
  }

  for (const job of state.jobs) {
    const row = document.createElement("article");
    row.className = "job-row";
    const identity = document.createElement("div");
    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = job.recording_title || `Recording ${job.recording_id}`;
    const status = document.createElement("span");
    status.className = `job-status ${job.status}`;
    status.textContent = job.status;
    identity.append(title, status);

    const meta = document.createElement("div");
    meta.className = "job-meta";
    const template = state.templates.find((item) => item.id === job.template_id);
    meta.textContent = `${template?.product || job.template_id} / ${job.mode} / revision ${job.revision}`;

    const counts = document.createElement("div");
    counts.className = "job-counts";
    counts.textContent = job.status === "ready" || job.status === "extracting"
      ? `Updated ${formatDate(job.updated_at)}`
      : `${job.found_count} found / ${job.unknown_count} unknown / ${job.warning_count} warnings`;

    const warningList = document.createElement("div");
    warningList.className = "job-counts";
    if (job.generation_warnings?.length) {
      warningList.textContent = job.generation_warnings.join(" ");
    }

    const actions = document.createElement("div");
    actions.className = "job-actions";
    if (job.status === "review") {
      actions.append(actionButton("Generate Word", () => runJobAction(job, "generate")));
    } else if (job.status === "completed") {
      const link = document.createElement("a");
      link.className = "button";
      link.href = `/api/scoping/jobs/${job.job_id}/document`;
      link.textContent = "Download DOCX";
      link.addEventListener("click", async (event) => {
        if (!token()) return;
        event.preventDefault();
        await downloadDocument(job);
      });
      actions.append(link);
    } else if (job.status === "failed") {
      const operation = job.failed_operation === "generation" ? "generate" : "extract";
      actions.append(actionButton(`Retry ${operation}`, () => runJobAction(job, operation), true));
    }
    row.append(identity, meta, counts);
    if (job.generation_warnings?.length) row.append(warningList);
    row.append(actions);
    container.append(row);
  }
}

async function runJobAction(job, operation) {
  try {
    const options = operation === "generate"
      ? { method: "POST", body: JSON.stringify({ include_inferred: false }) }
      : { method: "POST" };
    await api(`/api/scoping/jobs/${job.job_id}/${operation}`, options);
    showMessage(`${operation === "generate" ? "Document generation" : "Extraction"} started.`);
    await refresh();
  } catch (error) { handleError(error); }
}

async function downloadDocument(job) {
  try {
    const response = await fetch(`/api/scoping/jobs/${job.job_id}/document`, {
      headers: { Authorization: `Bearer ${token()}` },
    });
    if (!response.ok) throw new Error(`Download failed: ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${(job.recording_title || `recording-${job.recording_id}`).replace(/[^a-z0-9._-]+/gi, "_")}_${job.mode}.docx`;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (error) { handleError(error); }
}

function handleError(error) {
  setConnection(false, "Disconnected");
  if (error.status === 401 || error.status === 403) byId("token-panel").classList.remove("hidden");
  showMessage(error.message || String(error), true);
}

async function refresh() {
  try {
    const [templates, inbox, jobs] = await Promise.all([
      api("/api/scoping/templates"),
      api("/api/scoping/inbox"),
      api("/api/scoping/jobs?limit=100"),
    ]);
    state.templates = templates;
    state.inbox = inbox;
    state.jobs = jobs;
    renderInbox();
    renderJobs();
    setConnection(true, "Connected");
    byId("token-panel").classList.add("hidden");
    scheduleRefresh();
  } catch (error) { handleError(error); }
}

function scheduleRefresh() {
  window.clearTimeout(state.timer);
  const active = state.jobs.some((job) => ["extracting", "generating"].includes(job.status));
  if (active) state.timer = window.setTimeout(refresh, 3000);
}

byId("token-button").addEventListener("click", () => byId("token-panel").classList.toggle("hidden"));
byId("save-token").addEventListener("click", () => {
  sessionStorage.setItem("scopingApiToken", byId("api-token").value.trim());
  refresh();
});
byId("api-token").addEventListener("keydown", (event) => {
  if (event.key === "Enter") byId("save-token").click();
});
byId("refresh-button").addEventListener("click", refresh);
refresh();
