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
  maskMode: false,
  maskDraft: null,
  selectedMaskIndex: null,
  editUndoStack: [],
  editRedoStack: [],
  workflowPlanId: null,
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
    const blender = health.blender_available ? " · Blender" : "";
    const blendOption = $("#exportFormat")?.querySelector('option[value="blend"]');
    if (blendOption) blendOption.disabled = !health.blender_available;
    if (health.oda_available) {
      pill.textContent = `SVG · DXF · DWG${ocr}${blender} ready`;
      pill.className = "status-pill ready";
    } else {
      pill.textContent = `SVG · DXF${ocr}${blender} ready · ODA not found`;
      pill.className = "status-pill warn";
    }
  } catch (_) {
    $("#odaStatus").textContent = "Local service unavailable";
  }
}

function formatProjectDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function renderRecentProjects(projects) {
  const list = $("#recentProjectList");
  if (!projects.length) {
    list.innerHTML = `
      <p class="recent-empty">
        No projects yet. Start with a PDF or import a .planline file.
      </p>
    `;
    return;
  }
  list.innerHTML = projects.map((project) => `
    <article class="project-card">
      <button class="project-card-main" type="button"
        data-project-action="open" data-project-id="${escapeHtml(project.id)}">
        <strong>${escapeHtml(project.name)}</strong>
        <small>${project.page_count} ${project.page_count === 1 ? "page" : "pages"}
          · ${project.plan_count} ${project.plan_count === 1 ? "plan" : "plans"}
          · ${escapeHtml(formatProjectDate(project.updated_at))}</small>
      </button>
      <div class="project-card-actions" aria-label="${escapeHtml(project.name)} actions">
        <button type="button" data-project-action="rename"
          data-project-id="${escapeHtml(project.id)}"
          data-project-name="${escapeHtml(project.name)}">Rename</button>
        <button type="button" data-project-action="duplicate"
          data-project-id="${escapeHtml(project.id)}">Duplicate</button>
        <button type="button" data-project-action="archive"
          data-project-id="${escapeHtml(project.id)}">Archive</button>
        <button type="button" class="danger" data-project-action="delete"
          data-project-id="${escapeHtml(project.id)}"
          data-project-name="${escapeHtml(project.name)}">Delete</button>
      </div>
    </article>
  `).join("");
}

async function loadProjects() {
  try {
    renderRecentProjects(await api("/api/projects"));
  } catch (error) {
    $("#recentProjectList").innerHTML = `
      <p class="recent-empty">${escapeHtml(error.message)}</p>
    `;
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

  const plan = selectedPlan();
  if (plan) {
    (plan.exclude_regions || []).forEach((mask, index) => {
      drawMask(mask, `Exclude ${index + 1}`, index === state.selectedMaskIndex);
    });
  }
  if (state.maskDraft) drawMask(state.maskDraft, "New exclusion", true);

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

function normalizedRect(rect) {
  return {
    x0: Math.min(rect.x0, rect.x1),
    y0: Math.min(rect.y0, rect.y1),
    x1: Math.max(rect.x0, rect.x1),
    y1: Math.max(rect.y0, rect.y1),
  };
}

function drawMask(mask, label, selected = false) {
  const normalized = normalizedRect(mask);
  const a = pdfToCanvas(normalized.x0, normalized.y0);
  const b = pdfToCanvas(normalized.x1, normalized.y1);
  ctx.fillStyle = selected ? "rgba(242, 140, 40, .20)" : "rgba(184, 50, 57, .18)";
  ctx.strokeStyle = selected ? "#e87516" : "#b83239";
  ctx.lineWidth = selected ? 3 : 2;
  ctx.setLineDash(selected ? [] : [6, 4]);
  ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
  ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
  ctx.setLineDash([]);
  ctx.fillStyle = selected ? "#b85d00" : "#b83239";
  ctx.font = "700 10px system-ui";
  ctx.fillText(label, a.x + 5, a.y + 14);
  if (selected) drawRectHandles(a, b, "#e87516");
}

function cloneMasks(masks) {
  return (masks || []).map((mask) => ({ ...mask }));
}

function geometrySnapshot(plan = selectedPlan()) {
  if (!plan) return null;
  return {
    crop: { ...plan.crop },
    exclude_regions: cloneMasks(plan.exclude_regions),
  };
}

function updateEditHistoryButtons() {
  const planId = selectedPlan()?.id;
  const hasUndo = state.editUndoStack.some((entry) => entry.planId === planId);
  const hasRedo = state.editRedoStack.some((entry) => entry.planId === planId);
  $("#undoEditButton").disabled = !hasUndo;
  $("#redoEditButton").disabled = !hasRedo;
}

function pushEditHistory(before, after, selectedIndex = state.selectedMaskIndex) {
  const plan = selectedPlan();
  if (!plan || !before || !after) return;
  state.editUndoStack.push({
    planId: plan.id,
    before: {
      crop: { ...before.crop },
      exclude_regions: cloneMasks(before.exclude_regions),
    },
    after: {
      crop: { ...after.crop },
      exclude_regions: cloneMasks(after.exclude_regions),
    },
    selectedIndex,
  });
  const planEntries = state.editUndoStack.filter((entry) => entry.planId === plan.id);
  if (planEntries.length > 50) {
    const oldestPlanEntry = state.editUndoStack.findIndex(
      (entry) => entry.planId === plan.id
    );
    state.editUndoStack.splice(oldestPlanEntry, 1);
  }
  state.editRedoStack = state.editRedoStack.filter((entry) => entry.planId !== plan.id);
  updateEditHistoryButtons();
}

function popPlanHistory(stack, planId) {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    if (stack[index].planId === planId) return stack.splice(index, 1)[0];
  }
  return null;
}

async function applyEditHistory(entry, direction) {
  const plan = selectedPlan();
  if (!plan || !entry) return false;
  const geometry = direction === "undo" ? entry.before : entry.after;
  try {
    await savePlan({
      crop: { ...geometry.crop },
      exclude_regions: cloneMasks(geometry.exclude_regions),
    });
    state.selectedMaskIndex = entry.selectedIndex === null
      || geometry.exclude_regions.length === 0
      ? null
      : Math.min(entry.selectedIndex, geometry.exclude_regions.length - 1);
    renderInspector();
    drawCanvas();
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

async function undoPlanEdit() {
  const plan = selectedPlan();
  if (!plan) return;
  const entry = popPlanHistory(state.editUndoStack, plan.id);
  if (!entry) return;
  if (await applyEditHistory(entry, "undo")) {
    state.editRedoStack.push(entry);
    toast("Plan edit undone.");
  } else state.editUndoStack.push(entry);
  updateEditHistoryButtons();
}

async function redoPlanEdit() {
  const plan = selectedPlan();
  if (!plan) return;
  const entry = popPlanHistory(state.editRedoStack, plan.id);
  if (!entry) return;
  if (await applyEditHistory(entry, "redo")) {
    state.editUndoStack.push(entry);
    toast("Plan edit redone.");
  } else state.editRedoStack.push(entry);
  updateEditHistoryButtons();
}

function drawHandles(a, b) {
  drawRectHandles(a, b, "#1d6b4b");
}

function drawRectHandles(a, b, color) {
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  const points = [
    [a.x, a.y], [b.x, a.y], [b.x, b.y], [a.x, b.y],
  ];
  points.forEach(([x, y]) => {
    ctx.fillRect(x - 5, y - 5, 10, 10);
    ctx.strokeRect(x - 5, y - 5, 10, 10);
  });
}

function rectHit(event, source) {
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const normalized = normalizedRect(source);
  const a = pdfToCanvas(normalized.x0, normalized.y0);
  const b = pdfToCanvas(normalized.x1, normalized.y1);
  const threshold = 10;
  const near = (value, target) => Math.abs(value - target) <= threshold;
  if (near(x, a.x) && near(y, a.y)) return "nw";
  if (near(x, b.x) && near(y, a.y)) return "ne";
  if (near(x, b.x) && near(y, b.y)) return "se";
  if (near(x, a.x) && near(y, b.y)) return "sw";
  if (x >= a.x && x <= b.x && y >= a.y && y <= b.y) return "move";
  return null;
}

function cropHit(event) {
  const plan = selectedPlan();
  if (!plan) return null;
  return rectHit(event, plan.crop);
}

function selectedMaskHit(event) {
  const masks = selectedPlan()?.exclude_regions || [];
  const index = state.selectedMaskIndex;
  if (index === null || !masks[index]) return null;
  return rectHit(event, masks[index]);
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
  if (state.maskMode && selectedPlan()) {
    state.selectedMaskIndex = null;
    state.maskDraft = {
      x0: pdfPoint.x,
      y0: pdfPoint.y,
      x1: pdfPoint.x,
      y1: pdfPoint.y,
    };
    state.dragging = {
      mode: "exclude-mask",
      start: pdfPoint,
      beforeGeometry: geometrySnapshot(),
    };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    drawCanvas();
    return;
  }

  const maskHit = selectedMaskHit(event);
  if (maskHit) {
    const index = state.selectedMaskIndex;
    state.dragging = {
      mode: `mask-${maskHit}`,
      start: pdfPoint,
      index,
      mask: normalizedRect(selectedPlan().exclude_regions[index]),
      beforeGeometry: geometrySnapshot(),
    };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
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
      beforeGeometry: geometrySnapshot(),
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
  if (state.dragging.mode === "exclude-mask") {
    state.maskDraft.x1 = point.x;
    state.maskDraft.y1 = point.y;
    drawCanvas();
    return;
  }
  const dx = point.x - state.dragging.start.x;
  const dy = point.y - state.dragging.start.y;
  if (state.dragging.mode.startsWith("mask-")) {
    const action = state.dragging.mode.slice(5);
    const mask = { ...state.dragging.mask };
    if (action === "move") {
      mask.x0 += dx; mask.x1 += dx; mask.y0 += dy; mask.y1 += dy;
    } else {
      if (action.includes("w")) mask.x0 += dx;
      if (action.includes("e")) mask.x1 += dx;
      if (action.includes("n")) mask.y0 += dy;
      if (action.includes("s")) mask.y1 += dy;
    }
    selectedPlan().exclude_regions[state.dragging.index] = normalizedRect(mask);
    drawCanvas();
    return;
  }
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
  if (state.dragging.mode === "exclude-mask") {
    const beforeGeometry = state.dragging.beforeGeometry;
    state.dragging = null;
    const draft = state.maskDraft;
    state.maskDraft = null;
    state.maskMode = false;
    const mask = {
      x0: Math.min(draft.x0, draft.x1),
      y0: Math.min(draft.y0, draft.y1),
      x1: Math.max(draft.x0, draft.x1),
      y1: Math.max(draft.y0, draft.y1),
    };
    if (mask.x1 - mask.x0 < 2 || mask.y1 - mask.y0 < 2) {
      toast("Draw a larger exclusion area.", true);
      drawCanvas();
      return;
    }
    try {
      const newIndex = (selectedPlan().exclude_regions || []).length;
      await savePlan({
        exclude_regions: [...(selectedPlan().exclude_regions || []), mask],
      });
      pushEditHistory(beforeGeometry, geometrySnapshot(), newIndex);
      state.selectedMaskIndex = newIndex;
      renderInspector();
      drawCanvas();
      $("#canvasHint").textContent = "Exclusion mask saved. Draw another or export the plan.";
      toast("Exclusion mask saved.");
    } catch (error) {
      toast(error.message, true);
      await refreshJob();
    }
    return;
  }
  if (state.dragging.mode.startsWith("mask-")) {
    const index = state.dragging.index;
    const beforeGeometry = state.dragging.beforeGeometry;
    const masks = [...(selectedPlan().exclude_regions || [])];
    const edited = normalizedRect(masks[index]);
    state.dragging = null;
    if (edited.x1 - edited.x0 < 2 || edited.y1 - edited.y0 < 2) {
      toast("Exclusion masks must be at least 2 PDF points wide and high.", true);
      await refreshJob();
      return;
    }
    masks[index] = edited;
    try {
      await savePlan({ exclude_regions: masks });
      pushEditHistory(beforeGeometry, geometrySnapshot(), index);
      state.selectedMaskIndex = index;
      renderInspector();
      drawCanvas();
      $("#canvasHint").textContent = "Exclusion mask updated. Drag it again to keep editing.";
      toast("Exclusion mask updated.");
    } catch (error) {
      toast(error.message, true);
      await refreshJob();
    }
    return;
  }
  const beforeGeometry = state.dragging.beforeGeometry;
  state.dragging = null;
  try {
    await savePlan({ crop: selectedPlan().crop });
    pushEditHistory(beforeGeometry, geometrySnapshot(), null);
  } catch (error) {
    toast(error.message, true);
    await refreshJob();
  }
});

canvas.addEventListener("pointercancel", () => {
  if (state.dragging?.beforeGeometry && selectedPlan()) {
    selectedPlan().crop = { ...state.dragging.beforeGeometry.crop };
    selectedPlan().exclude_regions = cloneMasks(
      state.dragging.beforeGeometry.exclude_regions
    );
  }
  state.dragging = null;
  state.maskDraft = null;
  state.maskMode = false;
  canvas.classList.remove("panning");
  drawCanvas();
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
  const masks = plan.exclude_regions || [];
  $("#maskCount").textContent = `${masks.length} ${masks.length === 1 ? "mask" : "masks"}`;
  $("#maskList").innerHTML = masks.map((mask, index) => `
    <div class="mask-item ${index === state.selectedMaskIndex ? "selected" : ""}"
      data-select-mask="${index}" role="button" tabindex="0"
      aria-label="Edit exclusion mask ${index + 1}">
      <span>Exclude ${index + 1}</span>
      <small>${Math.round(mask.x1 - mask.x0)} × ${Math.round(mask.y1 - mask.y0)} PDF pt</small>
      <button type="button" data-remove-mask="${index}">Remove</button>
    </div>
  `).join("");
  updateEditHistoryButtons();
  $("#confirmScaleButton").textContent = plan.scale_confirmed ? "Scale confirmed ✓" : "Confirm scale";
  $("#confirmScaleButton").disabled = !plan.units_per_point;
  const measurementsComplete = Boolean(plan.scale_confirmed && plan.units_per_point);
  $("#measurementStep").classList.toggle("complete", measurementsComplete);
  $("#cleanupStep").classList.toggle("locked", !measurementsComplete);
  $("#measurementStatus").textContent = measurementsComplete
    ? `${plan.scale_label || "Calibrated"} · ${plan.units}`
    : "Set drawing scale";
  const activeCleanup = Number(plan.remove_text) + Number(plan.orthogonal_only) + masks.length;
  $("#cleanupStatus").textContent = measurementsComplete
    ? (activeCleanup ? `${activeCleanup} active setting${activeCleanup === 1 ? "" : "s"}` : "Optional export cleanup")
    : "Complete measurements first";
  if (state.workflowPlanId !== plan.id) {
    state.workflowPlanId = plan.id;
    $("#measurementStep").open = !measurementsComplete;
    $("#cleanupStep").open = measurementsComplete;
  }
  $("#planWarnings").innerHTML = plan.warnings
    .map((warning) => `<div class="warning-item">${escapeHtml(warning)}</div>`).join("");
}

function advanceToCleanup() {
  $("#measurementStep").open = false;
  $("#cleanupStep").classList.remove("locked");
  $("#cleanupStep").open = true;
  $("#cleanupStep").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderExportState() {
  const ready = state.job.plans.filter((plan) => plan.scale_confirmed && plan.units_per_point);
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
  state.maskMode = false;
  state.maskDraft = null;
  state.selectedMaskIndex = null;
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
  state.maskMode = false;
  state.maskDraft = null;
  state.selectedMaskIndex = null;
  state.workflowPlanId = null;
  renderInspector();
  drawCanvas();
}

async function refreshJob() {
  state.job = await api(`/api/jobs/${state.job.id}`);
  if (!state.job.plans.some((plan) => plan.id === state.planId)) {
    state.planId = pagePlans()[0]?.id || null;
  }
  const maskCount = selectedPlan()?.exclude_regions?.length || 0;
  if (state.selectedMaskIndex !== null && state.selectedMaskIndex >= maskCount) {
    state.selectedMaskIndex = null;
  }
  renderPages();
  renderInspector();
  renderExportState();
  drawCanvas();
}

function setProjectSaveStatus(message, stateName = "") {
  const status = $("#projectSaveStatus");
  status.textContent = message;
  status.className = `project-save-status ${stateName}`.trim();
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
  setProjectSaveStatus("Saving…", "saving");
  let updated;
  try {
    updated = await api(`/api/jobs/${state.job.id}/plans/${plan.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setProjectSaveStatus("Saved locally");
  } catch (error) {
    setProjectSaveStatus("Save failed", "error");
    throw error;
  }
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
  const projectName = $("#newProjectName").value.trim();
  if (projectName) form.append("name", projectName);
  try {
    state.job = await api("/api/projects", { method: "POST", body: form });
    if (state.job.status === "error") throw new Error(state.job.error);
    $("#uploadView").classList.add("hidden");
    $("#workspaceView").classList.remove("hidden");
    $("#jobName").textContent = state.job.project_name || state.job.filename;
    $("#exportProjectButton").href = `/api/projects/${state.job.id}/package`;
    window.history.replaceState(null, "", `/?job=${state.job.id}`);
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

async function handleProjectImport(file) {
  if (!file) return;
  $("#uploadError").classList.add("hidden");
  $("#uploadError").textContent = "";
  const form = new FormData();
  form.append("file", file);
  try {
    $("#projectFileInput").disabled = true;
    state.job = await api("/api/projects/import", { method: "POST", body: form });
    window.location.href = `/?job=${state.job.id}`;
  } catch (error) {
    $("#uploadError").textContent = error.message;
    $("#uploadError").classList.remove("hidden");
    toast(error.message, true);
  } finally {
    $("#projectFileInput").disabled = false;
    $("#projectFileInput").value = "";
  }
}

async function loadExistingJob() {
  const jobId = new URLSearchParams(window.location.search).get("job");
  if (!jobId) {
    await loadProjects();
    return;
  }
  try {
    state.job = await api(`/api/jobs/${jobId}`);
    $("#uploadView").classList.add("hidden");
    $("#workspaceView").classList.remove("hidden");
    $("#jobName").textContent = state.job.project_name || state.job.filename;
    $("#exportProjectButton").href = `/api/projects/${state.job.id}/package`;
    state.pageIndex = state.job.pages.find((page) => !page.raster_only)?.index || 0;
    state.planId = state.job.plans.find((plan) => plan.page_index === state.pageIndex)?.id || null;
    renderExportState();
    await selectPage(state.pageIndex);
  } catch (error) {
    toast(error.message, true);
    window.history.replaceState(null, "", "/");
    await loadProjects();
  }
}

$("#fileInput").addEventListener("change", (event) => handleUpload(event.target.files[0]));
$("#projectFileInput").addEventListener(
  "change",
  (event) => handleProjectImport(event.target.files[0]),
);
["dragenter", "dragover"].forEach((name) => $("#dropZone").addEventListener(name, (event) => {
  event.preventDefault(); $("#dropZone").classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => $("#dropZone").addEventListener(name, (event) => {
  event.preventDefault(); $("#dropZone").classList.remove("dragging");
}));
$("#dropZone").addEventListener("drop", (event) => handleUpload(event.dataTransfer.files[0]));
$("#refreshProjectsButton").addEventListener("click", loadProjects);

$("#recentProjectList").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("[data-project-action]");
  if (!actionButton) return;
  const projectId = actionButton.dataset.projectId;
  const action = actionButton.dataset.projectAction;
  if (action === "open") {
    window.location.href = `/?job=${projectId}`;
    return;
  }
  try {
    if (action === "rename") {
      const name = window.prompt("Rename project", actionButton.dataset.projectName || "");
      if (name === null) return;
      if (!name.trim()) throw new Error("Project name cannot be empty.");
      await api(`/api/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      toast("Project renamed.");
    } else if (action === "duplicate") {
      await api(`/api/projects/${projectId}/duplicate`, { method: "POST" });
      toast("Project duplicated.");
    } else if (action === "archive") {
      await api(`/api/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      });
      toast("Project archived.");
    } else if (action === "delete") {
      const name = actionButton.dataset.projectName || "this project";
      if (!window.confirm(`Permanently delete “${name}”?`)) return;
      await api(`/api/projects/${projectId}`, { method: "DELETE" });
      toast("Project deleted.");
    }
    await loadProjects();
  } catch (error) {
    toast(error.message, true);
  }
});

$("#planForm").addEventListener("submit", (event) => event.preventDefault());

$("#confirmScaleButton").addEventListener("click", async () => {
  try {
    await savePlan({ scale_confirmed: true, confirmed: true });
    advanceToCleanup();
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
    advanceToCleanup();
    toast("Detected scale selected and plan saved.");
  } catch (error) { toast(error.message, true); }
});

$("#cleanupStep").addEventListener("click", (event) => {
  if (!$("#cleanupStep").classList.contains("locked")) return;
  event.preventDefault();
  $("#measurementStep").open = true;
  toast("Complete measurements before opening cleanup.", true);
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

$("#drawMaskButton").addEventListener("click", () => {
  if (!selectedPlan()) return;
  state.calibrationMode = false;
  state.maskMode = true;
  state.maskDraft = null;
  state.selectedMaskIndex = null;
  $("#canvasHint").textContent = "Drag a rectangle over anything that should be excluded from export.";
  toast("Drag an exclusion mask on the drawing.");
});

$("#clearMasksButton").addEventListener("click", async () => {
  if (!selectedPlan()?.exclude_regions?.length) return;
  const beforeGeometry = geometrySnapshot();
  try {
    await savePlan({ exclude_regions: [] });
    pushEditHistory(beforeGeometry, geometrySnapshot(), null);
    state.selectedMaskIndex = null;
    renderInspector();
    drawCanvas();
    toast("All exclusion masks removed.");
  } catch (error) { toast(error.message, true); }
});

$("#undoEditButton").addEventListener("click", undoPlanEdit);
$("#redoEditButton").addEventListener("click", redoPlanEdit);

$("#maskList").addEventListener("click", async (event) => {
  const remove = event.target.closest("[data-remove-mask]");
  if (!remove) {
    const item = event.target.closest("[data-select-mask]");
    if (!item) return;
    state.selectedMaskIndex = Number(item.dataset.selectMask);
    state.maskMode = false;
    state.maskDraft = null;
    renderInspector();
    drawCanvas();
    $("#canvasHint").textContent = "Drag the selected mask or its corner handles to edit it.";
    toast(`Exclude ${state.selectedMaskIndex + 1} selected for editing.`);
    return;
  }
  event.stopPropagation();
  const index = Number(remove.dataset.removeMask);
  const beforeGeometry = geometrySnapshot();
  const masks = [...(selectedPlan().exclude_regions || [])];
  masks.splice(index, 1);
  try {
    await savePlan({ exclude_regions: masks });
    pushEditHistory(beforeGeometry, geometrySnapshot(), null);
    if (state.selectedMaskIndex === index) state.selectedMaskIndex = null;
    else if (state.selectedMaskIndex > index) state.selectedMaskIndex -= 1;
    renderInspector();
    drawCanvas();
    toast("Exclusion mask removed.");
  } catch (error) { toast(error.message, true); }
});

$("#maskList").addEventListener("keydown", (event) => {
  if (!["Enter", " "].includes(event.key)) return;
  const item = event.target.closest("[data-select-mask]");
  if (!item || event.target.closest("[data-remove-mask]")) return;
  event.preventDefault();
  item.click();
});

$("#pickPointsButton").addEventListener("click", () => {
  state.calibration = [];
  state.calibrationMode = true;
  state.maskMode = false;
  state.maskDraft = null;
  state.selectedMaskIndex = null;
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
  const editingField = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName);
  const command = event.metaKey || event.ctrlKey;
  if (command && !editingField && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redoPlanEdit();
    else undoPlanEdit();
    return;
  }
  if (command && !editingField && event.key.toLowerCase() === "y") {
    event.preventDefault();
    redoPlanEdit();
    return;
  }
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
    advanceToCleanup();
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
  window.location.href = "/";
});

window.addEventListener("resize", sizeCanvas);
new ResizeObserver(sizeCanvas).observe($("#canvasShell"));
checkHealth();
loadExistingJob();
