const form = document.querySelector("#lookup-form");
const submit = document.querySelector("#lookup-submit");
const errorBox = document.querySelector("#lookup-error");
const copyButton = document.querySelector("#copy-deal");
let selectedDeal = null;
let timer = null;

form.addEventListener("submit", startLookup);
copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(selectedDeal);
    copyButton.textContent = "Copied";
  } catch (_) {
    showError("Copy is unavailable in this browser. Select the Deal ID above to copy it.");
  }
});

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg).join("; ") : detail;
    throw new Error(message || "The request could not be completed.");
  }
  return payload;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function setText(id, value) {
  document.getElementById(id).textContent = value || "";
}

async function startLookup(event) {
  event.preventDefault();
  clearTimeout(timer);
  errorBox.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Starting…";
  try {
    const job = await request("/api/deal-manager/lookups", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    history.replaceState({}, "", `/deal-manager/lookups/${job.lookup_id}`);
    render(job);
    poll(job);
    loadHistory();
  } catch (error) {
    showError(error.message);
    submit.disabled = false;
    submit.textContent = "Find Deal ID";
  }
}

function render(job) {
  document.querySelector("#lookup-run").classList.remove("hidden");
  setText("lookup-id", `Lookup ${job.lookup_id}`);
  setText("lookup-status", job.status);
  const pending = job.status === "queued" || job.status === "running";
  document.querySelector("#lookup-progress-details").open = pending;
  submit.disabled = pending;
  submit.textContent = pending ? "Finding deal…" : "Find Deal ID";
  const progress = document.querySelector("#lookup-progress");
  progress.replaceChildren();
  for (const message of job.progress) {
    const item = document.createElement("li");
    item.textContent = message;
    progress.append(item);
  }
  if (job.error) showError(job.error);
  else errorBox.classList.add("hidden");
  document.querySelector("#lookup-result").classList.toggle("hidden", !job.result);
  if (job.result) renderResult(job.result);
}

function renderResult(result) {
  selectedDeal = result.deal_id;
  const labels = {needs_review: "More information needed", no_match: "No matching deal found", error: "Lookup could not be completed"};
  setText("lookup-answer", result.status === "matched"
    ? `Deal ${result.deal_id}${result.deal_title ? ` · ${result.deal_title}` : ""}`
    : labels[result.status]);
  setText("lookup-reason", result.reason);
  setText("lookup-next", result.next_check ? `Next step: ${result.next_check}` : "");
  setText("lookup-json", JSON.stringify(result, null, 2));
  copyButton.classList.toggle("hidden", !selectedDeal);
  copyButton.textContent = "Copy Deal ID";
  const candidates = document.querySelector("#lookup-candidates");
  candidates.replaceChildren();
  if (!selectedDeal) {
    for (const candidate of result.candidate_deals) {
      const item = document.createElement("p");
      item.textContent = `Candidate ${candidate.deal_id}: ${candidate.supports.join(" ")} ${candidate.conflicts.join(" ")}`;
      candidates.append(item);
    }
  }
}

function poll(job) {
  if (job.status !== "queued" && job.status !== "running") {
    loadHistory();
    return;
  }
  timer = setTimeout(async () => {
    try {
      const next = await request(`/api/deal-manager/lookups/${job.lookup_id}`);
      render(next);
      poll(next);
    } catch (error) {
      showError(`${error.message} Retrying…`);
      poll(job);
    }
  }, 2000);
}

async function loadHistory() {
  try {
    const jobs = await request("/api/deal-manager/lookups");
    const list = document.querySelector("#lookup-history");
    list.replaceChildren();
    if (!jobs.length) list.textContent = "No lookups yet.";
    for (const job of jobs) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/deal-manager/lookups/${job.lookup_id}`;
      link.textContent = `${new Date(job.created_at).toLocaleString()} · ${job.result?.status || job.status}${job.result?.deal_id ? ` · Deal ${job.result.deal_id}` : ""}`;
      item.append(link);
      list.append(item);
    }
  } catch (error) {
    showError(error.message);
  }
}

async function restoreLookup() {
  const match = location.pathname.match(/^\/deal-manager\/lookups\/([a-f0-9]{32})$/);
  if (!match) return;
  try {
    const job = await request(`/api/deal-manager/lookups/${match[1]}`);
    for (const [name, value] of Object.entries(job.input)) form.elements[name].value = value;
    render(job);
    poll(job);
  } catch (error) {
    showError(error.message);
  }
}

loadHistory();
restoreLookup();
