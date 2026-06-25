const state = {
  job: null,
  pageIndex: 0,
  planId: null,
  image: null,
  imageUrl: null,
  view: {
    scale: 1,
    fitScale: 1,
    zoom: 1,
    x: 0,
    y: 0,
    initialized: false,
  },
  dragging: null,
  calibration: [],
  calibrationMode: false,
  panMode: false,
  spaceDown: false,
};

const $ = (selector) => document.querySelector(selector);
const canvas = $("#planCanvas");
const ctx = canvas.getContext("2d");

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.add("hidden"), 4200);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function checkHealth() {
  try {
    const health = await api("/api/health");
    const pill = $("#odaStatus");
    const ocr = health.ocr_available ? " · OCR" : "";
    if (health.oda_available) {
      pill.textContent = `SVG · DXF · DWG${ocr} ready`;
      pill.className = "status-pill ready";
    } else {
      pill.textContent = `SVG · DXF${ocr} ready · ODA not found`;
      pill.className = "status-pill warn";
    }
  } catch (_) {
    $("#odaStatus").textContent = "Local service unavailable";
  }
}

function currentPage() {
  return state.job?.pages.find((page) => page.index === state.pageIndex);
}

function pagePlans() {
  return state.job?.plans.filter((plan) => plan.page_index === state.pageIndex) || [];
}

function selectedPlan() {
  return state.job?.plans.find((plan) => plan.id === state.planId) || null;
}

function pdfToCanvas(x, y) {
  return {
    x: state.view.x + x * state.view.scale,
    y: state.view.y + y * state.view.scale,
  };
}

function canvasToPdf(x, y) {
  return {
    x: (x - state.view.x) / state.view.scale,
    y: (y - state.view.y) / state.view.scale,
  };
}

function updateZoomUI() {
  $("#zoomLevel").textContent = `${Math.round(state.view.zoom * 100)}%`;
  $("#panButton").classList.toggle("active", state.panMode);
  canvas.classList.toggle("pan-mode", state.panMode || state.spaceDown);
}

function fitPage(redraw = true) {
  const page = currentPage();
  if (!page || !canvas.clientWidth || !canvas.clientHeight) return;
  const scale = Math.min(
    (canvas.clientWidth - 52) / page.width,
    (canvas.clientHeight - 52) / page.height,
  );
  state.view = {
    scale,
    fitScale: scale,
    zoom: 1,
    x: (canvas.clientWidth - page.width * scale) / 2,
    y: (canvas.clientHeight - page.height * scale) / 2,
    initialized: true,
  };
  updateZoomUI();
  if (redraw) drawCanvas();
}

function zoomAt(canvasX, canvasY, factor) {
  const page = currentPage();
  if (!page || !state.view.initialized) return;
  const oldScale = state.view.scale;
  const minimum = state.view.fitScale * 0.5;
  const maximum = state.view.fitScale * 16;
  const nextScale = Math.max(minimum, Math.min(maximum, oldScale * factor));
  if (Math.abs(nextScale - oldScale) < 1e-9) return;
  const pdfX = (canvasX - state.view.x) / oldScale;
  const pdfY = (canvasY - state.view.y) / oldScale;
  state.view.scale = nextScale;
  state.view.zoom = nextScale / state.view.fitScale;
  state.view.x = canvasX - pdfX * nextScale;
  state.view.y = canvasY - pdfY * nextScale;
  updateZoomUI();
  drawCanvas();
}

function zoomFromCenter(factor) {
  zoomAt(canvas.clientWidth / 2, canvas.clientHeight / 2, factor);
}

function sizeCanvas() {
  const shell = $("#canvasShell");
  const ratio = window.devicePixelRatio || 1;
  const previousWidth = canvas.clientWidth;
  const previousHeight = canvas.clientHeight;
  const centerPdf = state.view.initialized && previousWidth && previousHeight
    ? canvasToPdf(previousWidth / 2, previousHeight / 2)
    : null;
  canvas.width = Math.max(1, Math.round(shell.clientWidth * ratio));
  canvas.height = Math.max(1, Math.round(shell.clientHeight * ratio));
  canvas.style.width = `${shell.clientWidth}px`;
  canvas.style.height = `${shell.clientHeight}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (state.view.initialized && currentPage()) {
    const page = currentPage();
    state.view.fitScale = Math.min(
      (shell.clientWidth - 52) / page.width,
      (shell.clientHeight - 52) / page.height,
    );
    state.view.scale = state.view.fitScale * state.view.zoom;
    if (centerPdf) {
      state.view.x = shell.clientWidth / 2 - centerPdf.x * state.view.scale;
      state.view.y = shell.clientHeight / 2 - centerPdf.y * state.view.scale;
    }
  }
  drawCanvas();
}

function drawCanvas() {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  if (!state.image || !currentPage()) return;

  const page = currentPage();
  if (!state.view.initialized) fitPage(false);
  const scale = state.view.scale;
  ctx.fillStyle = "#fff";
  ctx.fillRect(state.view.x, state.view.y, page.width * scale, page.height * scale);
  ctx.drawImage(state.image, state.view.x, state.view.y, page.width * scale, page.height * scale);

  pagePlans().forEach((plan) => {
    const a = pdfToCanvas(plan.crop.x0, plan.crop.y0);
    const b = pdfToCanvas(plan.crop.x1, plan.crop.y1);
    const selected = plan.id === state.planId;
    const low = plan.confidence < 0.6;
    ctx.fillStyle = low ? "rgba(210,134,40,.10)" : "rgba(29,107,75,.09)";
    ctx.strokeStyle = selected ? "#b5e358" : low ? "#d28628" : "#1d6b4b";
    ctx.lineWidth = selected ? 3 : 2;
    ctx.setLineDash(selected ? [] : [7, 4]);
    ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.setLineDash([]);
    ctx.fillStyle = selected ? "#b5e358" : low ? "#d28628" : "#1d6b4b";
    ctx.fillRect(a.x, a.y - 24, Math.min(180, Math.max(80, plan.name.length * 6.5)), 24);
    ctx.fillStyle = selected ? "#152118" : "#fff";
    ctx.font = "600 11px system-ui";
    ctx.fillText(plan.name.slice(0, 24), a.x + 7, a.y - 8);
    if (selected) drawHandles(a, b);
  });

  if (state.calibration.length) {
    ctx.strokeStyle = "#d43f67";
    ctx.fillStyle = "#d43f67";
    ctx.lineWidth = 2;
    const points = state.calibration.map((point) => pdfToCanvas(point.x, point.y));
    points.forEach((point, index) => {
      ctx.beginPath();
      ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillText(String(index + 1), point.x + 8, point.y - 8);
    });
    if (points.length === 2) {
      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      ctx.lineTo(points[1].x, points[1].y);
      ctx.stroke();
    }
  }
}

function drawHandles(a, b) {
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#1d6b4b";
  const points = [
    [a.x, a.y], [b.x, a.y], [b.x, b.y], [a.x, b.y],
  ];
  points.forEach(([x, y]) => {
    ctx.fillRect(x - 5, y - 5, 10, 10);
    ctx.strokeRect(x - 5, y - 5, 10, 10);
  });
}

function cropHit(event) {
  const plan = selectedPlan();
  if (!plan) return null;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const a = pdfToCanvas(plan.crop.x0, plan.crop.y0);
  const b = pdfToCanvas(plan.crop.x1, plan.crop.y1);
  const threshold = 10;
  const near = (value, target) => Math.abs(value - target) <= threshold;
  if (near(x, a.x) && near(y, a.y)) return "nw";
  if (near(x, b.x) && near(y, a.y)) return "ne";
  if (near(x, b.x) && near(y, b.y)) return "se";
  if (near(x, a.x) && near(y, b.y)) return "sw";
  if (x >= a.x && x <= b.x && y >= a.y && y <= b.y) return "move";
  return null;
}

canvas.addEventListener("pointerdown", (event) => {
  const rect = canvas.getBoundingClientRect();
  const canvasPoint = {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
  const pdfPoint = canvasToPdf(event.clientX - rect.left, event.clientY - rect.top);
  if (state.panMode || state.spaceDown || event.button === 1) {
    state.dragging = {
      mode: "pan",
      startCanvas: canvasPoint,
      x: state.view.x,
      y: state.view.y,
    };
    canvas.classList.add("panning");
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    return;
  }
  if (state.calibrationMode) {
    if (state.calibration.length === 2) state.calibration = [];
    state.calibration.push(pdfPoint);
    if (state.calibration.length === 2) {
      state.calibrationMode = false;
      $("#applyCalibrationButton").disabled = false;
      $("#canvasHint").textContent = "Enter the real-world distance, then apply calibration.";
    }
    drawCanvas();
    return;
  }

  const plans = [...pagePlans()].reverse();
  const clicked = plans.find((plan) => {
    const a = pdfToCanvas(plan.crop.x0, plan.crop.y0);
    const b = pdfToCanvas(plan.crop.x1, plan.crop.y1);
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    return x >= a.x && x <= b.x && y >= a.y && y <= b.y;
  });
  if (clicked && clicked.id !== state.planId) {
    selectPlan(clicked.id);
    return;
  }
  const hit = cropHit(event);
  if (hit && selectedPlan()) {
    state.dragging = {
      mode: hit,
      start: pdfPoint,
      crop: { ...selectedPlan().crop },
    };
    canvas.setPointerCapture(event.pointerId);
  }
});

canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  const rect = canvas.getBoundingClientRect();
  if (state.dragging.mode === "pan") {
    state.view.x = state.dragging.x + (event.clientX - rect.left - state.dragging.startCanvas.x);
    state.view.y = state.dragging.y + (event.clientY - rect.top - state.dragging.startCanvas.y);
    drawCanvas();
    return;
  }
  const point = canvasToPdf(event.clientX - rect.left, event.clientY - rect.top);
  const dx = point.x - state.dragging.start.x;
  const dy = point.y - state.dragging.start.y;
  const crop = { ...state.dragging.crop };
  if (state.dragging.mode === "move") {
    crop.x0 += dx; crop.x1 += dx; crop.y0 += dy; crop.y1 += dy;
  } else {
    if (state.dragging.mode.includes("w")) crop.x0 += dx;
    if (state.dragging.mode.includes("e")) crop.x1 += dx;
    if (state.dragging.mode.includes("n")) crop.y0 += dy;
    if (state.dragging.mode.includes("s")) crop.y1 += dy;
  }
  selectedPlan().crop = crop;
  drawCanvas();
});

canvas.addEventListener("pointerup", async (event) => {
  if (!state.dragging) return;
  canvas.releasePointerCapture(event.pointerId);
  if (state.dragging.mode === "pan") {
    state.dragging = null;
    canvas.classList.remove("panning");
    return;
  }
  state.dragging = null;
  try {
    await savePlan({ crop: selectedPlan().crop, confirmed: false });
  } catch (error) {
    toast(error.message, true);
    await refreshJob();
  }
});

canvas.addEventListener("pointercancel", () => {
  state.dragging = null;
  canvas.classList.remove("panning");
});

canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const factor = Math.exp(-event.deltaY * 0.0015);
  zoomAt(event.clientX - rect.left, event.clientY - rect.top, factor);
}, { passive: false });

function renderPages() {
  const list = $("#pageList");
  list.innerHTML = "";
  state.job.pages.forEach((page) => {
    const plans = state.job.plans.filter((plan) => plan.page_index === page.index);
    const button = document.createElement("button");
    button.className = `page-button ${page.index === state.pageIndex ? "active" : ""}`;
    button.innerHTML = `
      <img class="page-thumb" src="/api/jobs/${state.job.id}/pages/${page.index}/preview" alt="">
      <span class="page-meta"><strong>Page ${page.index + 1}</strong>
      <small>${page.raster_only ? "Raster-only" : `${page.vector_items} vector items`}</small></span>
      <span class="count-badge ${page.raster_only ? "warning" : ""}">${plans.length}</span>`;
    button.addEventListener("click", () => selectPage(page.index));
    list.appendChild(button);
  });
}

function renderInspector() {
  const plan = selectedPlan();
  $("#noPlan").classList.toggle("hidden", Boolean(plan));
  $("#planForm").classList.toggle("hidden", !plan);
  if (!plan) return;
  $("#planTitle").textContent = plan.name;
  $("#planName").value = plan.name;
  $("#rotation").value = String(plan.rotation);
  $("#units").value = plan.units;
  $("#scaleLabel").textContent = plan.scale_label || "Not detected";
  $("#scaleValue").textContent = plan.units_per_point
    ? `${plan.units_per_point.toPrecision(7)} ${plan.units} per PDF point`
    : "Calibration required";
  const candidates = [];
  const seen = new Set();
  for (const candidate of currentPage().scale_candidates || []) {
    const key = `${candidate.units}:${candidate.units_per_point.toFixed(9)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push(candidate);
  }
  const candidateSelect = $("#scaleCandidate");
  candidateSelect.innerHTML = candidates.map((candidate, index) => {
    const confidence = Math.round(candidate.confidence * 100);
    return `<option value="${index}">${escapeHtml(candidate.label)} · ${confidence}%</option>`;
  }).join("");
  const selectedIndex = candidates.findIndex((candidate) =>
    candidate.units === plan.units
    && Math.abs(candidate.units_per_point - plan.units_per_point) < 1e-9
  );
  if (selectedIndex >= 0) candidateSelect.value = String(selectedIndex);
  candidateSelect.dataset.candidates = JSON.stringify(candidates);
  $("#scaleCandidateRow").classList.toggle("hidden", candidates.length < 2);
  const selectedCandidate = selectedIndex >= 0 ? candidates[selectedIndex] : null;
  $("#scaleSource").textContent = selectedCandidate
    ? `${selectedCandidate.source} · ${Math.round(selectedCandidate.confidence * 100)}% confidence`
    : "";
  $("#removeText").checked = Boolean(plan.remove_text);
  $("#orthogonalOnly").checked = Boolean(plan.orthogonal_only);
  $("#angleTolerance").value = String(plan.angle_tolerance || 3);
  $("#angleTolerance").disabled = !plan.orthogonal_only;
  $("#confirmScaleButton").textContent = plan.scale_confirmed ? "Scale confirmed ✓" : "Confirm scale";
  $("#confirmScaleButton").disabled = !plan.units_per_point;
  $("#planWarnings").innerHTML = plan.warnings
    .map((warning) => `<div class="warning-item">${escapeHtml(warning)}</div>`).join("");
}

function renderExportState() {
  const ready = state.job.plans.filter((plan) => plan.confirmed && plan.scale_confirmed && plan.units_per_point);
  $("#exportCount").textContent = `${ready.length} ${ready.length === 1 ? "plan" : "plans"} ready`;
  $("#exportButton").disabled = ready.length === 0;
}

function showExportProgress() {
  $("#exportProgress").classList.remove("hidden");
  $("#progressTitle").textContent = "Building vector files";
  $("#progressPercent").textContent = "0%";
  $("#progressBar").style.width = "0%";
  $("#progressStep").textContent = "Starting…";
  $("#progressLog").innerHTML = "";
  $("#closeProgressButton").classList.add("hidden");
  $("#downloadExportButton").classList.add("hidden");
}

function renderProgress(payload) {
  const progress = Number(payload.progress || 0);
  $("#progressPercent").textContent = `${progress}%`;
  $("#progressBar").style.width = `${progress}%`;
  $("#progressStep").textContent = payload.step || "Working…";
  $("#progressLog").innerHTML = (payload.events || []).map((event) => {
    const error = event.toLowerCase().includes("failed");
    return `<div class="progress-line ${error ? "error" : ""}">${escapeHtml(event)}</div>`;
  }).join("");
  $("#progressLog").scrollTop = $("#progressLog").scrollHeight;
}

async function monitorExport() {
  while (true) {
    const payload = await api(`/api/jobs/${state.job.id}/export/status`);
    renderProgress(payload);
    if (payload.status === "complete") {
      $("#progressTitle").textContent = "Vector export complete";
      $("#downloadExportButton").href = `/api/jobs/${state.job.id}/download`;
      $("#downloadExportButton").classList.remove("hidden");
      $("#closeProgressButton").classList.remove("hidden");
      await refreshJob();
      return;
    }
    if (payload.status === "error") {
      $("#progressTitle").textContent = "Export stopped";
      $("#closeProgressButton").classList.remove("hidden");
      throw new Error(payload.error || "Export failed.");
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

async function loadPageImage() {
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  const response = await fetch(`/api/jobs/${state.job.id}/pages/${state.pageIndex}/preview`);
  const blob = await response.blob();
  state.imageUrl = URL.createObjectURL(blob);
  state.image = new Image();
  state.image.onload = () => {
    $("#canvasEmpty").classList.add("hidden");
    fitPage();
  };
  state.image.src = state.imageUrl;
}

async function selectPage(index) {
  state.pageIndex = index;
  const plans = pagePlans();
  state.planId = plans[0]?.id || null;
  state.calibration = [];
  state.calibrationMode = false;
  state.view.initialized = false;
  const page = currentPage();
  $("#pageLabel").textContent = `Page ${index + 1}`;
  $("#pageStats").textContent = page.raster_only
    ? "Raster-only sheet · tracing is not supported"
    : `${page.vector_paths} paths · ${page.vector_items} vector items`;
  renderPages();
  renderInspector();
  await loadPageImage();
}

function selectPlan(planId) {
  state.planId = planId;
  state.calibration = [];
  state.calibrationMode = false;
  renderInspector();
  drawCanvas();
}

async function refreshJob() {
  state.job = await api(`/api/jobs/${state.job.id}`);
  if (!state.job.plans.some((plan) => plan.id === state.planId)) {
    state.planId = pagePlans()[0]?.id || null;
  }
  renderPages();
  renderInspector();
  renderExportState();
  drawCanvas();
}

async function savePlan(overrides = {}) {
  const plan = selectedPlan();
  if (!plan) return;
  const payload = {
    name: $("#planName").value.trim() || plan.name,
    rotation: Number($("#rotation").value),
    units: $("#units").value,
    ...overrides,
  };
  const updated = await api(`/api/jobs/${state.job.id}/plans/${plan.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const index = state.job.plans.findIndex((item) => item.id === plan.id);
  state.job.plans[index] = updated;
  state.planId = updated.id;
  renderInspector();
  renderPages();
  renderExportState();
  drawCanvas();
}

async function handleUpload(file) {
  if (!file) return;
  $("#uploadError").classList.add("hidden");
  $("#uploadError").textContent = "";
  $("#dropZone strong").textContent = "Analyzing vector geometry…";
  const form = new FormData();
  form.append("file", file);
  try {
    state.job = await api("/api/jobs", { method: "POST", body: form });
    if (state.job.status === "error") throw new Error(state.job.error);
    $("#uploadView").classList.add("hidden");
    $("#workspaceView").classList.remove("hidden");
    $("#jobName").textContent = state.job.filename;
    state.pageIndex = state.job.pages.find((page) => !page.raster_only)?.index || 0;
    state.planId = state.job.plans.find((plan) => plan.page_index === state.pageIndex)?.id || null;
    renderExportState();
    await selectPage(state.pageIndex);
    if (!state.job.plans.length) toast("No vector plan regions were detected.", true);
  } catch (error) {
    $("#uploadError").textContent = error.message;
    $("#uploadError").classList.remove("hidden");
    toast(error.message, true);
  } finally {
    $("#dropZone strong").textContent = "Drop a PDF planset here";
  }
}

async function loadExistingJob() {
  const jobId = new URLSearchParams(window.location.search).get("job");
  if (!jobId) return;
  try {
    state.job = await api(`/api/jobs/${jobId}`);
    $("#uploadView").classList.add("hidden");
    $("#workspaceView").classList.remove("hidden");
    $("#jobName").textContent = state.job.filename;
    state.pageIndex = state.job.pages.find((page) => !page.raster_only)?.index || 0;
    state.planId = state.job.plans.find((plan) => plan.page_index === state.pageIndex)?.id || null;
    renderExportState();
    await selectPage(state.pageIndex);
  } catch (error) {
    toast(error.message, true);
  }
}

$("#fileInput").addEventListener("change", (event) => handleUpload(event.target.files[0]));
["dragenter", "dragover"].forEach((name) => $("#dropZone").addEventListener(name, (event) => {
  event.preventDefault(); $("#dropZone").classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => $("#dropZone").addEventListener(name, (event) => {
  event.preventDefault(); $("#dropZone").classList.remove("dragging");
}));
$("#dropZone").addEventListener("drop", (event) => handleUpload(event.dataTransfer.files[0]));

$("#planForm").addEventListener("submit", (event) => event.preventDefault());

$("#confirmScaleButton").addEventListener("click", async () => {
  try {
    await savePlan({ scale_confirmed: true, confirmed: true });
    toast("Scale confirmed and plan saved.");
  } catch (error) { toast(error.message, true); }
});

$("#scaleCandidate").addEventListener("change", async (event) => {
  const candidates = JSON.parse(event.target.dataset.candidates || "[]");
  const candidate = candidates[Number(event.target.value)];
  if (!candidate) return;
  try {
    await savePlan({
      units: candidate.units,
      units_per_point: candidate.units_per_point,
      scale_label: candidate.label,
      scale_confirmed: true,
      confirmed: true,
    });
    toast("Detected scale selected and plan saved.");
  } catch (error) { toast(error.message, true); }
});

$("#removeText").addEventListener("change", async (event) => {
  try {
    await savePlan({ remove_text: event.target.checked });
    toast(event.target.checked ? "Text cleanup enabled." : "Text cleanup disabled.");
  } catch (error) { toast(error.message, true); }
});

$("#orthogonalOnly").addEventListener("change", async (event) => {
  try {
    await savePlan({ orthogonal_only: event.target.checked });
    toast(event.target.checked ? "Orthogonal line filter enabled." : "All line angles restored.");
  } catch (error) { toast(error.message, true); }
});

$("#angleTolerance").addEventListener("change", async (event) => {
  try {
    await savePlan({ angle_tolerance: Number(event.target.value) });
    toast(`Angle tolerance set to ±${event.target.value}°.`);
  } catch (error) { toast(error.message, true); }
});

$("#pickPointsButton").addEventListener("click", () => {
  state.calibration = [];
  state.calibrationMode = true;
  $("#applyCalibrationButton").disabled = true;
  $("#canvasHint").textContent = "Scroll to zoom, use Hand or Space to pan, then click the two known points.";
  drawCanvas();
});

$("#zoomInButton").addEventListener("click", () => zoomFromCenter(1.35));
$("#zoomOutButton").addEventListener("click", () => zoomFromCenter(1 / 1.35));
$("#fitPageButton").addEventListener("click", () => fitPage());
$("#panButton").addEventListener("click", () => {
  state.panMode = !state.panMode;
  updateZoomUI();
});

window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  state.spaceDown = true;
  updateZoomUI();
  event.preventDefault();
});

window.addEventListener("keyup", (event) => {
  if (event.code !== "Space") return;
  state.spaceDown = false;
  canvas.classList.remove("panning");
  updateZoomUI();
});

$("#applyCalibrationButton").addEventListener("click", async () => {
  const distance = Number($("#knownDistance").value);
  if (state.calibration.length !== 2 || !(distance > 0)) {
    toast("Choose two points and enter a positive distance.", true); return;
  }
  const [a, b] = state.calibration;
  try {
    const updated = await api(`/api/jobs/${state.job.id}/plans/${state.planId}/calibrate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        distance, units: $("#knownUnits").value,
        name: $("#planName").value.trim() || selectedPlan().name,
        crop: selectedPlan().crop,
        rotation: Number($("#rotation").value),
      }),
    });
    const index = state.job.plans.findIndex((plan) => plan.id === updated.id);
    state.job.plans[index] = updated;
    state.calibration = [];
    renderInspector(); renderExportState(); drawCanvas();
    toast("Calibration applied and plan saved.");
  } catch (error) { toast(error.message, true); }
});

$("#deletePlanButton").addEventListener("click", async () => {
  if (!selectedPlan()) return;
  try {
    await api(`/api/jobs/${state.job.id}/plans/${state.planId}`, { method: "DELETE" });
    await refreshJob();
  } catch (error) { toast(error.message, true); }
});

$("#addPlanButton").addEventListener("click", async () => {
  const page = currentPage();
  const margin = Math.min(page.width, page.height) * .08;
  try {
    const plan = await api(`/api/jobs/${state.job.id}/plans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        page_index: state.pageIndex,
        crop: { x0: margin, y0: margin, x1: page.width - margin, y1: page.height - margin },
      }),
    });
    state.job.plans.push(plan);
    state.planId = plan.id;
    renderPages(); renderInspector(); drawCanvas();
  } catch (error) { toast(error.message, true); }
});

$("#exportButton").addEventListener("click", async () => {
  const button = $("#exportButton");
  button.disabled = true;
  showExportProgress();
  try {
    await api(`/api/jobs/${state.job.id}/export/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: $("#exportFormat").value }),
    });
    await monitorExport();
  } catch (error) {
    $("#progressStep").textContent = error.message;
    $("#closeProgressButton").classList.remove("hidden");
    toast(error.message, true);
  } finally {
    renderExportState();
  }
});

$("#closeProgressButton").addEventListener("click", () => {
  $("#exportProgress").classList.add("hidden");
});

$("#newJobButton").addEventListener("click", async () => {
  if (state.job) {
    try { await api(`/api/jobs/${state.job.id}`, { method: "DELETE" }); } catch (_) {}
  }
  window.location.reload();
});

window.addEventListener("resize", sizeCanvas);
new ResizeObserver(sizeCanvas).observe($("#canvasShell"));
checkHealth();
loadExistingJob();
