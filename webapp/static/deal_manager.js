const form = document.querySelector("#lookup-form");
const submit = document.querySelector("#lookup-submit");
const errorBox = document.querySelector("#lookup-error");
const copyButton = document.querySelector("#copy-deal");
const openDealLink = document.querySelector("#open-deal");
const lookupActions = document.querySelector("#lookup-actions");
let selectedDeal = null;
let timer = null;
let chaining = false;

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

function chainKey(lookupId) {
  return `chain-am-${lookupId}`;
}

function markForAccountManagerChain(lookupId) {
  sessionStorage.setItem(chainKey(lookupId), "1");
}

function shouldChainAccountManager(lookupId) {
  return sessionStorage.getItem(chainKey(lookupId)) === "1";
}

function clearAccountManagerChain(lookupId) {
  sessionStorage.removeItem(chainKey(lookupId));
}

function composeClientMessage(input) {
  const envelope = [
    ["Channel", "Email"],
    ["From", (input.sender_email || "").trim()],
    ["Subject", (input.subject || "").trim()],
  ];
  const header = envelope
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${value}`)
    .join("\n");
  const body = (input.body || "").trim();
  return header ? `${header}\n\n${body}` : body;
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
    markForAccountManagerChain(job.lookup_id);
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
  if (!chaining) {
    submit.disabled = pending;
    submit.textContent = pending ? "Finding deal…" : "Find Deal ID";
  }
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
  if (!chaining) {
    setText("lookup-next", result.next_check ? `Next step: ${result.next_check}` : "");
  }
  setText("lookup-json", JSON.stringify(result, null, 2));
  lookupActions.classList.toggle("hidden", !selectedDeal);
  copyButton.textContent = "Copy Deal ID";
  if (selectedDeal) {
    openDealLink.href = `/deals/${selectedDeal}`;
  }
  const candidates = document.querySelector("#lookup-candidates");
  candidates.replaceChildren();
  if (!selectedDeal) {
    for (const candidate of result.candidate_deals) {
      const item = document.createElement("p");
      const link = document.createElement("a");
      link.href = `/deals/${candidate.deal_id}`;
      link.textContent = `Deal ${candidate.deal_id}`;
      item.append(
        link,
        document.createTextNode(
          `: ${candidate.supports.join(" ")} ${candidate.conflicts.join(" ")}`.trimEnd()
        ),
      );
      candidates.append(item);
    }
  }
}

function poll(job) {
  if (job.status !== "queued" && job.status !== "running") {
    loadHistory();
    maybeStartAccountManager(job);
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

async function maybeStartAccountManager(job) {
  if (chaining) return;
  if (job.status !== "completed" || job.result?.status !== "matched" || !job.result.deal_id) {
    clearAccountManagerChain(job.lookup_id);
    return;
  }
  if (!shouldChainAccountManager(job.lookup_id)) return;

  chaining = true;
  submit.disabled = true;
  submit.textContent = "Starting Account Manager…";
  setText("lookup-next", "Matched. Starting Account Manager with this email…");
  try {
    const input = job.input || Object.fromEntries(new FormData(form));
    const clientMessage = composeClientMessage(input);
    const run = await request("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        deal_id: job.result.deal_id,
        client_message: clientMessage,
        file_urls: [],
      }),
    });
    sessionStorage.setItem(`am-handoff-${run.run_id}`, JSON.stringify({
      deal_id: job.result.deal_id,
      from: input.sender_email || "",
      subject: input.subject || "",
      how: "Found by Deal Manager lookup",
      channel: "Email",
    }));
    clearAccountManagerChain(job.lookup_id);
    window.location.href = `/runs/${run.run_id}`;
  } catch (error) {
    chaining = false;
    showError(`Deal found, but Account Manager could not start: ${error.message}`);
    setText("lookup-next", "Open the deal below to start Account Manager manually.");
    submit.disabled = false;
    submit.textContent = "Find Deal ID";
  }
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
    for (const [name, value] of Object.entries(job.input)) {
      if (form.elements[name] && value != null) form.elements[name].value = value;
    }
    render(job);
    poll(job);
  } catch (error) {
    showError(error.message);
  }
}

loadHistory();
restoreLookup();
