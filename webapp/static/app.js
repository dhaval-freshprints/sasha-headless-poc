const views = {
  dashboard: document.querySelector("#dashboard-view"),
  deal: document.querySelector("#deal-view"),
  run: document.querySelector("#run-view"),
};

const addDealForm = document.querySelector("#add-deal-form");
const addDealButton = document.querySelector("#add-deal-button");
const addDealError = document.querySelector("#add-deal-error");
const runForm = document.querySelector("#run-form");
const workflowSelect = document.querySelector("#workflow");
const runFormCard = document.querySelector("#run-form-card");
const submitButton = document.querySelector("#submit-button");
const formError = document.querySelector("#form-error");
const runError = document.querySelector("#run-error");

let currentDealId = "";
let pollTimer = null;

addDealForm.addEventListener("submit", addDeal);
runForm.addEventListener("submit", startRun);
workflowSelect.addEventListener("change", updateMessageRequirement);
document.querySelector("#start-run-button").addEventListener("click", showRunForm);
document.querySelector("#cancel-run-button").addEventListener("click", hideRunForm);
document.addEventListener("click", handleAppLink);
window.addEventListener("popstate", loadPageFromUrl);

loadPageFromUrl();

async function addDeal(event) {
  event.preventDefault();
  hideError(addDealError);
  addDealButton.disabled = true;
  addDealButton.textContent = "Adding…";
  const dealId = document.querySelector("#new-deal-id").value.trim();

  try {
    const response = await fetch("/api/deals", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({deal_id: dealId}),
    });
    const deal = await readResponse(response);
    document.querySelector("#new-deal-id").value = "";
    navigate(`/deals/${deal.deal_id}`);
  } catch (error) {
    showError(addDealError, error.message);
  } finally {
    addDealButton.disabled = false;
    addDealButton.textContent = "Add deal";
  }
}

async function startRun(event) {
  event.preventDefault();
  hideError(formError);
  submitButton.disabled = true;
  submitButton.textContent = "Starting…";

  const message = document.querySelector("#client-message").value;
  const fileUrls = document.querySelector("#file-urls").value
    .split("\n")
    .map((value) => value.trim())
    .filter(Boolean);

  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        deal_id: currentDealId,
        workflow: workflowSelect.value,
        client_message: message,
        file_urls: fileUrls,
      }),
    });
    const run = await readResponse(response);
    clearRunForm();
    navigate(`/runs/${run.run_id}`, run);
  } catch (error) {
    showError(formError, error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Run Sasha";
  }
}

function loadPageFromUrl() {
  stopPolling();
  const runMatch = window.location.pathname.match(/^\/runs\/([a-f0-9]+)$/);
  if (runMatch) {
    loadRun(runMatch[1]);
    return;
  }

  const dealMatch = window.location.pathname.match(/^\/deals\/([0-9]+)$/);
  if (dealMatch) {
    loadDeal(dealMatch[1]);
    return;
  }

  loadDashboard();
}

async function loadDashboard() {
  showView("dashboard");
  document.title = "Deals · Sasha";
  hideError(addDealError);

  try {
    const response = await fetch("/api/deals");
    renderDeals(await readResponse(response));
  } catch (error) {
    showError(addDealError, error.message);
  }
}

async function loadDeal(dealId) {
  showView("deal");
  currentDealId = dealId;
  hideRunForm();
  hideError(formError);
  setText("#detail-deal-id", dealId);
  document.title = `Deal ${dealId} · Sasha`;

  try {
    const response = await fetch(`/api/deals/${encodeURIComponent(dealId)}`);
    const deal = await readResponse(response);
    renderDealHistory(deal);
  } catch (error) {
    document.querySelector("#deal-runs-list").replaceChildren();
    document.querySelector("#no-runs").classList.remove("hidden");
    showError(formError, error.message);
    showRunForm();
  }
}

async function loadRun(runId, initialRun = null) {
  showView("run");
  hideError(runError);

  try {
    const run = initialRun || await fetchRun(runId);
    renderRun(run);
    if (!isFinished(run.status)) {
      schedulePoll(runId);
    }
  } catch (error) {
    showError(runError, error.message);
  }
}

async function fetchRun(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  return readResponse(response);
}

function schedulePoll(runId) {
  stopPolling();
  pollTimer = window.setTimeout(async () => {
    try {
      const run = await fetchRun(runId);
      renderRun(run);
      if (!isFinished(run.status)) {
        schedulePoll(runId);
      }
    } catch (error) {
      showError(runError, `${error.message} Retrying…`);
      schedulePoll(runId);
    }
  }, 2000);
}

function renderDeals(deals) {
  const list = document.querySelector("#deal-list");
  const empty = document.querySelector("#no-deals");
  list.replaceChildren();
  empty.classList.toggle("hidden", deals.length !== 0);

  for (const deal of deals) {
    const link = document.createElement("a");
    link.className = "deal-card";
    link.href = `/deals/${deal.deal_id}`;
    link.dataset.appLink = "";

    const heading = document.createElement("div");
    heading.className = "deal-card-heading";
    const title = document.createElement("h2");
    title.textContent = `Deal ${deal.deal_id}`;
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    heading.append(title, arrow);

    const detail = document.createElement("p");
    detail.textContent = runCountText(deal.run_count);
    const latest = document.createElement("p");
    latest.className = "deal-latest";
    latest.textContent = deal.latest_run
      ? `${capitalize(deal.latest_run.status)} · ${formatDate(deal.latest_run.created_at)}`
      : "Ready for its first run";
    link.append(heading, detail, latest);
    list.append(link);
  }
}

function renderDealHistory(deal) {
  currentDealId = deal.deal_id;
  setText("#detail-deal-id", deal.deal_id);
  setText("#deal-run-summary", runCountText(deal.run_count));
  const list = document.querySelector("#deal-runs-list");
  const empty = document.querySelector("#no-runs");
  list.replaceChildren();
  empty.classList.toggle("hidden", deal.runs.length !== 0);

  for (const run of deal.runs) {
    const link = document.createElement("a");
    link.className = "run-row";
    link.href = `/runs/${run.run_id}`;
    link.dataset.appLink = "";

    const main = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = runTitle(run);
    const time = document.createElement("span");
    time.textContent = formatDate(run.created_at);
    main.append(title, time);

    const status = document.createElement("span");
    status.className = `status status-${run.status}`;
    status.textContent = capitalize(run.status);
    link.append(main, status);
    list.append(link);
  }
}

function renderRun(run) {
  currentDealId = run.deal_id;
  document.title = `Run ${shortId(run.run_id)} · Sasha`;
  document.querySelector("#back-to-deal").href = `/deals/${run.deal_id}`;
  setText("#run-id", shortId(run.run_id));
  setText("#run-deal-id", run.deal_id);
  setText("#elapsed", formatDuration(currentElapsedSeconds(run)));
  setText("#started-at", formatDate(run.started_at));
  setText("#finished-at", formatDate(run.finished_at));

  const badge = document.querySelector("#status-badge");
  badge.textContent = capitalize(run.status);
  badge.className = `status status-${run.status}`;

  renderProgress(run.progress);
  renderResult(run);
  renderArtifacts(run);

  if (run.error_message) {
    showError(runError, run.error_message);
  } else {
    hideError(runError);
  }

  document.querySelector("#refresh-note").textContent = isFinished(run.status)
    ? "Run finished"
    : "Safe to refresh or close this page";
}

function renderProgress(messages) {
  const list = document.querySelector("#progress-list");
  list.replaceChildren();
  for (const message of messages) {
    const item = document.createElement("li");
    item.textContent = message;
    list.append(item);
  }
}

function renderResult(run) {
  const section = document.querySelector("#result-section");
  if (!run.result) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");
  document.querySelector("#message-preview").srcdoc = run.result.message_html || "";
  document.querySelector("#result-json").textContent = JSON.stringify(run.result, null, 2);
}

function renderArtifacts(run) {
  const section = document.querySelector("#artifacts-section");
  const list = document.querySelector("#artifact-list");
  list.replaceChildren();
  if (!run.artifacts.length) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");
  for (const artifact of run.artifacts) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/api/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodePath(artifact)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = artifact;
    item.append(link);
    list.append(item);
  }
}

function showRunForm() {
  runFormCard.classList.remove("hidden");
  workflowSelect.focus();
}

function updateMessageRequirement() {
  document.querySelector("#client-message").required =
    workflowSelect.value === "client-response-orchestrator";
}

function runTitle(run) {
  switch (run.workflow) {
    case "outreach":
      return "Initial outreach";
    case "client-response-orchestrator":
      return truncate(run.client_message || "Client response", 72);
    default:
      return truncate(run.client_message || "Previous run", 72);
  }
}

function hideRunForm() {
  runFormCard.classList.add("hidden");
  hideError(formError);
}

function clearRunForm() {
  runForm.reset();
  updateMessageRequirement();
  hideRunForm();
}

function showView(name) {
  for (const [viewName, element] of Object.entries(views)) {
    element.classList.toggle("hidden", viewName !== name);
  }
}

function handleAppLink(event) {
  const link = event.target.closest("[data-app-link]");
  if (!link || link.origin !== window.location.origin) {
    return;
  }
  event.preventDefault();
  navigate(link.pathname);
}

function navigate(path, initialRun = null) {
  history.pushState({}, "", path);
  if (initialRun) {
    stopPolling();
    loadRun(initialRun.run_id, initialRun);
    return;
  }
  loadPageFromUrl();
}

async function readResponse(response) {
  const payload = await response.json();
  if (response.ok) {
    return payload;
  }
  const detail = Array.isArray(payload.detail)
    ? payload.detail.map((item) => item.msg).join("; ")
    : payload.detail;
  throw new Error(detail || `Request failed with status ${response.status}`);
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function isFinished(status) {
  return status === "completed" || status === "failed";
}

function showError(element, message) {
  element.textContent = message;
  element.classList.remove("hidden");
}

function hideError(element) {
  element.textContent = "";
  element.classList.add("hidden");
}

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

function shortId(value) {
  return value ? value.slice(0, 12) : "—";
}

function capitalize(value) {
  return value ? value[0].toUpperCase() + value.slice(1) : "Unknown";
}

function formatDuration(seconds) {
  if (seconds === null) {
    return "—";
  }
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const remaining = rounded % 60;
  return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function currentElapsedSeconds(run) {
  if (run.elapsed_seconds) {
    return run.elapsed_seconds;
  }
  if (run.started_at && !isFinished(run.status)) {
    return Math.max(0, (Date.now() - new Date(run.started_at).getTime()) / 1000);
  }
  return null;
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  return new Date(value).toLocaleString();
}

function encodePath(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

function runCountText(count) {
  return `${count} ${count === 1 ? "run" : "runs"}`;
}

function truncate(value, length) {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}
