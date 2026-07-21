const state = { jobs: [], sections: [] };
const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function setStatus(label, isOnline) {
  const pill = byId("status-pill");
  pill.textContent = label;
  pill.classList.toggle("online", isOnline);
}

function showMessage(message, isError = false) {
  const element = byId("message");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  window.setTimeout(() => element.classList.add("hidden"), 6000);
}

function fillSectionSelect(select) {
  select.replaceChildren();

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Saved default";
  select.append(defaultOption);

  for (const section of state.sections) {
    const option = document.createElement("option");
    option.value = section.section_id;
    option.textContent = section.is_saved_default ? `${section.path} (saved)` : section.path;
    select.append(option);
  }
}

function render() {
  const body = byId("jobs-body");
  const empty = byId("empty-state");
  body.replaceChildren();

  byId("summary").textContent = `${state.jobs.length} pending ${state.jobs.length === 1 ? "job" : "jobs"}`;
  empty.classList.toggle("hidden", state.jobs.length > 0);

  for (const job of state.jobs) {
    const row = byId("job-row-template").content.firstElementChild.cloneNode(true);
    row.querySelector(".job-title").textContent = job.meeting_title;
    row.querySelector(".recording-id").textContent = job.recording_id;
    row.querySelector(".event-name").textContent = job.event;
    row.querySelector(".created-at").textContent = formatDate(job.created_at);

    const error = row.querySelector(".job-error");
    if (job.last_error) {
      error.textContent = job.last_error;
      error.classList.remove("hidden");
    }

    const sectionSelect = row.querySelector(".section-select");
    const saveDefault = row.querySelector(".save-default-input");
    fillSectionSelect(sectionSelect);
    sectionSelect.addEventListener("change", () => {
      saveDefault.disabled = !sectionSelect.value;
      if (!sectionSelect.value) saveDefault.checked = false;
    });
    saveDefault.disabled = true;

    row.querySelector(".route-button").addEventListener("click", () => routeJob(job, row));
    row.querySelector(".delete-button").addEventListener("click", () => deleteJob(job, row));
    body.append(row);
  }
}

async function refresh() {
  setStatus("Loading", false);
  try {
    const [jobs, sections] = await Promise.all([
      api("/api/queue/jobs"),
      api("/api/queue/sections"),
    ]);
    state.jobs = jobs;
    state.sections = sections;
    setStatus("Connected", true);
    render();
  } catch (error) {
    setStatus("Error", false);
    showMessage(error.message, true);
  }
}

async function routeJob(job, row) {
  const button = row.querySelector(".route-button");
  const sectionSelect = row.querySelector(".section-select");
  const saveDefault = row.querySelector(".save-default-input");
  button.disabled = true;
  button.textContent = "Routing";
  try {
    const result = await api(`/api/queue/jobs/${job.job_id}/route`, {
      method: "POST",
      body: JSON.stringify({
        section_id: sectionSelect.value || null,
        save_as_default: saveDefault.checked,
      }),
    });
    showMessage(`Routed "${job.meeting_title}" to ${result.section.path}.`);
    await refresh();
  } catch (error) {
    showMessage(error.message, true);
    await refresh();
  } finally {
    button.disabled = false;
    button.textContent = "Route";
  }
}

async function deleteJob(job, row) {
  if (!window.confirm(`Delete "${job.meeting_title}" from the queue?`)) return;
  const button = row.querySelector(".delete-button");
  button.disabled = true;
  button.textContent = "Deleting";
  try {
    await api(`/api/queue/jobs/${job.job_id}`, { method: "DELETE" });
    showMessage(`Deleted "${job.meeting_title}".`);
    await refresh();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Delete";
  }
}

byId("refresh-button").addEventListener("click", refresh);
refresh();
