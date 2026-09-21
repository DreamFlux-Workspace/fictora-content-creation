/** @typedef {Record<string, unknown>} Json */

let activeDeskId = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* plain text error */
  }
  if (!response.ok) {
    throw new Error(typeof body === "string" ? body : JSON.stringify(body.detail || body));
  }
  return body;
}

function setStatus(message) {
  document.getElementById("status").textContent = message;
}

function renderDesk(desk) {
  activeDeskId = desk.desk_id;
  document.getElementById("desk-id").textContent = `${desk.title} · ${desk.desk_id} · spine ${desk.spine_id || "—"}`;
  const gatesEl = document.getElementById("gates");
  gatesEl.innerHTML = "";

  const steps = [
    { key: "plates", label: "Plates", enrol: "/cast/enrol", approve: "/gates/plates/approve" },
    { key: "script", label: "Script", approve: "/gates/script/approve" },
    { key: "board", label: "Board", enrol: "/boards/enrol", exposure: true, approve: "/gates/board/approve" },
    { key: "video", label: "Take", estimate: true, enrol: "/video/enrol" },
  ];

  for (const step of steps) {
    const gate = desk.gates[step.key] || { status: "pending" };
    const row = document.createElement("div");
    row.className = "gate-row";
    const statusClass = gate.status === "approved" ? "status-approved" : "status-pending";
    row.innerHTML = `<strong>${step.label}</strong><span class="${statusClass}">${gate.status}</span>`;
    if (step.enrol) {
      const btn = document.createElement("button");
      btn.textContent = `Enrol ${step.label.toLowerCase()}`;
      btn.className = "secondary";
      btn.onclick = () => runAction(`${step.enrol}`, "POST");
      row.appendChild(btn);
    }
    if (step.exposure) {
      const btn = document.createElement("button");
      btn.textContent = "Measure exposure";
      btn.className = "secondary";
      btn.onclick = () => runAction("/boards/exposure", "GET");
      row.appendChild(btn);
    }
    if (step.estimate) {
      const btn = document.createElement("button");
      btn.textContent = "Estimate";
      btn.className = "secondary";
      btn.onclick = () => runAction("/estimate", "POST");
      row.appendChild(btn);
    }
    if (step.approve) {
      const btn = document.createElement("button");
      btn.textContent = `Approve ${step.label.toLowerCase()}`;
      btn.onclick = () => {
        const acceptDim =
          step.key === "board" &&
          desk.exposure &&
          Array.isArray(desk.exposure.boards) &&
          desk.exposure.boards.some((b) => b.below_dim_floor);
        const body =
          step.key === "board" ? JSON.stringify({ accept_dim: acceptDim }) : undefined;
        runAction(step.approve, "POST", body);
      };
      row.appendChild(btn);
    }
    gatesEl.appendChild(row);
  }

  document.getElementById("exposure").textContent = desk.exposure
    ? `Exposure:\n${JSON.stringify(desk.exposure, null, 2)}`
    : "";
  document.getElementById("estimate").textContent = desk.estimate
    ? `Estimate:\n${JSON.stringify(desk.estimate, null, 2)}`
    : "";

  const mediaEl = document.getElementById("media");
  mediaEl.innerHTML = "";
  const urls = [
    ...(desk.media?.cast_urls || []),
    ...(desk.media?.board_urls || []),
  ];
  for (const url of urls) {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "production still";
    mediaEl.appendChild(img);
  }

  const deliveryEl = document.getElementById("delivery");
  deliveryEl.innerHTML = "";
  if (desk.delivery) {
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(desk.delivery, null, 2);
    deliveryEl.appendChild(pre);
    const videoUrl =
      desk.delivery?.episodes?.[0]?.video_url ||
      desk.delivery?.video_url ||
      desk.delivery?.url;
    if (videoUrl) {
      const video = document.createElement("video");
      video.controls = true;
      video.src = videoUrl;
      video.style.width = "100%";
      video.style.marginTop = "0.75rem";
      deliveryEl.appendChild(video);
    }
  }
}

async function runAction(suffix, method, body) {
  if (!activeDeskId) return;
  setStatus(`Running ${method} ${suffix}…`);
  try {
    const desk = await api(`/api/desks/${activeDeskId}${suffix}`, {
      method,
      body: body || (method === "POST" ? "{}" : undefined),
    });
    renderDesk(desk);
    setStatus(`Done ${suffix}`);
    await refreshDeskList();
  } catch (err) {
    setStatus(String(err));
  }
}

async function refreshDeskList() {
  const desks = await api("/api/desks");
  const list = document.getElementById("desk-list");
  list.innerHTML = "";
  for (const desk of desks) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.textContent = `${desk.title} (${desk.desk_id}) — waiting ${desk.waiting_gate}`;
    btn.onclick = async () => {
      const fresh = await api(`/api/desks/${desk.desk_id}`);
      renderDesk(fresh);
    };
    li.appendChild(btn);
    list.appendChild(li);
  }
}

document.getElementById("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form).entries());
  setStatus("Starting draft…");
  try {
    const desk = await api("/api/desks", { method: "POST", body: JSON.stringify(data) });
    renderDesk(desk);
    setStatus("Draft complete. Run cast enrol when ready.");
    await refreshDeskList();
  } catch (err) {
    setStatus(String(err));
  }
});

async function boot() {
  try {
    const config = await api("/api/config");
    document.getElementById("config-line").textContent = `API ${config.drama_api_base_url} · data ${config.data_dir} · token ${config.token_configured === "True" ? "ok" : "missing"}`;
  } catch (err) {
    document.getElementById("config-line").textContent = String(err);
  }
  await refreshDeskList();
}

boot();
