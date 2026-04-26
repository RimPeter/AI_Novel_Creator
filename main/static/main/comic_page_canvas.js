(() => {
  const rootPanel = document.querySelector("[data-canvas-panel]");
  const layoutInput = document.querySelector("[data-canvas-layout-input]");
  const pageEditor = document.querySelector(".comic-page-editor");
  const pageForm = document.querySelector(".comic-page-form");
  const menuVisibilityToggle = document.querySelector("[data-canvas-menu-visibility-toggle]");
  const canvasResetMenu = document.querySelector("[data-canvas-reset-menu]");
  const canvasResetCancelButton = document.querySelector("[data-canvas-reset-cancel]");
  const canvasResetConfirmButton = document.querySelector("[data-canvas-reset-confirm]");
  const canvasNodeUrlTemplate = pageEditor?.dataset.canvasNodeUrlTemplate || "";
  const canvasGenerateUrlTemplate = pageEditor?.dataset.canvasGenerateUrlTemplate || "";
  const canvasQuickPromptUrlTemplate = pageEditor?.dataset.canvasQuickPromptUrlTemplate || "";
  const canvasQuickPromptAcceptUrlTemplate = pageEditor?.dataset.canvasQuickPromptAcceptUrlTemplate || "";
  const canvasQuickPromptRejectUrlTemplate = pageEditor?.dataset.canvasQuickPromptRejectUrlTemplate || "";
  if (!rootPanel || !(layoutInput instanceof HTMLInputElement) || !(pageEditor instanceof HTMLElement)) return;

  const MIN_PANEL_SIZE = 80;
  const MIN_BUBBLE_SIZE = 42;
  let splitIdSequence = 0;
  let canvasKeySequence = 0;
  let speechBubbleSequence = 0;
  let draggedCanvasPanel = null;
  const usedCanvasKeys = new Set();
  const pendingQuickPrompts = new Map();

  const updateMenuVisibilityButton = () => {
    if (!(menuVisibilityToggle instanceof HTMLButtonElement)) return;
    const isHidden = pageEditor.classList.contains("is-canvas-menu-hidden");
    menuVisibilityToggle.textContent = isHidden ? "Show Canvass Menu" : "Hide Canvass Menu";
    menuVisibilityToggle.setAttribute("aria-pressed", isHidden ? "true" : "false");
  };

  const buildCanvasNodeUrl = (canvasKey) =>
    canvasNodeUrlTemplate && canvasKey ? canvasNodeUrlTemplate.replace("__canvas_key__", encodeURIComponent(canvasKey)) : "";
  const buildCanvasGenerateUrl = (canvasKey) =>
    canvasGenerateUrlTemplate && canvasKey ? canvasGenerateUrlTemplate.replace("__canvas_key__", encodeURIComponent(canvasKey)) : "";
  const buildCanvasQuickPromptUrl = (canvasKey) =>
    canvasQuickPromptUrlTemplate && canvasKey ? canvasQuickPromptUrlTemplate.replace("__canvas_key__", encodeURIComponent(canvasKey)) : "";
  const buildCanvasQuickPromptAcceptUrl = (canvasKey) =>
    canvasQuickPromptAcceptUrlTemplate && canvasKey
      ? canvasQuickPromptAcceptUrlTemplate.replace("__canvas_key__", encodeURIComponent(canvasKey))
      : "";
  const buildCanvasQuickPromptRejectUrl = (canvasKey) =>
    canvasQuickPromptRejectUrlTemplate && canvasKey
      ? canvasQuickPromptRejectUrlTemplate.replace("__canvas_key__", encodeURIComponent(canvasKey))
      : "";

  const registerCanvasKey = (key) => {
    const normalized = String(key || "").trim();
    if (!normalized) return "";
    usedCanvasKeys.add(normalized);
    const match = normalized.match(/^canvas-(\d+)$/);
    if (match) {
      canvasKeySequence = Math.max(canvasKeySequence, Number(match[1]) || 0);
    }
    return normalized;
  };

  const nextCanvasKey = () => {
    do {
      canvasKeySequence += 1;
    } while (usedCanvasKeys.has(`canvas-${canvasKeySequence}`));
    return registerCanvasKey(`canvas-${canvasKeySequence}`);
  };

  const assignCanvasKey = (panel, key) => {
    if (!(panel instanceof HTMLElement)) return "";
    const current = (panel.dataset.canvasKey || "").trim();
    const desired = String(key || "").trim();
    if (current && current !== desired && panel.dataset.canvasKeyRegistered === "true") {
      usedCanvasKeys.delete(current);
    }
    let nextKey = desired || nextCanvasKey();
    if (usedCanvasKeys.has(nextKey) && current !== nextKey) {
      nextKey = nextCanvasKey();
    } else {
      registerCanvasKey(nextKey);
    }
    panel.dataset.canvasKey = nextKey;
    panel.dataset.canvasKeyRegistered = "true";
    return nextKey;
  };

  const ensureCanvasKey = (panel, fallback = "") => {
    if (!(panel instanceof HTMLElement)) return "";
    const normalizedFallback = String(fallback || "").trim();
    if (normalizedFallback) return assignCanvasKey(panel, normalizedFallback);
    const existing = (panel.dataset.canvasKey || "").trim();
    if (existing && panel.dataset.canvasKeyRegistered === "true") return existing;
    if (existing && !usedCanvasKeys.has(existing)) return assignCanvasKey(panel, existing);
    return assignCanvasKey(panel, "");
  };

  const ensureMenuToggleListener = (menu) => {
    if (!(menu instanceof HTMLDetailsElement) || menu.dataset.menuLayerSyncBound === "true") return;
    menu.dataset.menuLayerSyncBound = "true";
    menu.addEventListener("toggle", () => {
      syncCanvasMenuLayering();
    });
  };

  const createMenu = (ownerPanel) => {
    const details = document.createElement("details");
    details.className = "comic-canvas-menu";
    ensureMenuToggleListener(details);

    const summary = document.createElement("summary");
    summary.className = "comic-canvas-menu-toggle";
    summary.textContent = "Canvas menu";

    const panel = document.createElement("div");
    panel.className = "comic-canvas-menu-panel";

    const canvasEditUrl = buildCanvasNodeUrl(ensureCanvasKey(ownerPanel));
    const canvasGenerateUrl = buildCanvasGenerateUrl(ensureCanvasKey(ownerPanel));
    const canvasQuickPromptUrl = buildCanvasQuickPromptUrl(ensureCanvasKey(ownerPanel));
    if (canvasEditUrl) {
      const link = document.createElement("a");
      link.className = "comic-canvas-menu-action comic-canvas-menu-link";
      link.href = canvasEditUrl;
      link.textContent = "Edit canvas brief";
      panel.appendChild(link);
    }
    if (canvasGenerateUrl) {
      const generateButton = document.createElement("button");
      generateButton.type = "button";
      generateButton.className = "comic-canvas-menu-action";
      generateButton.dataset.canvasAction = "generate";
      generateButton.textContent = "Generate";
      panel.appendChild(generateButton);
    }
    if (canvasQuickPromptUrl) {
      const quickPromptButton = document.createElement("button");
      quickPromptButton.type = "button";
      quickPromptButton.className = "comic-canvas-menu-action";
      quickPromptButton.dataset.canvasAction = "show-quick-prompt";
      quickPromptButton.textContent = "Quick Prompt";
      panel.appendChild(quickPromptButton);
    }

    const speechBubbleButton = document.createElement("button");
    speechBubbleButton.type = "button";
    speechBubbleButton.className = "comic-canvas-menu-action";
    speechBubbleButton.dataset.canvasAction = "add-speech-bubble";
    speechBubbleButton.textContent = "Add speech bubble";
    panel.appendChild(speechBubbleButton);

    for (const option of [
      { direction: "horizontal", label: "Split Horizontally" },
      { direction: "vertical", label: "Split Vertically" },
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "comic-canvas-menu-action";
      button.dataset.splitDirection = option.direction;
      button.textContent = option.label;
      panel.appendChild(button);
    }

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "comic-canvas-menu-action comic-canvas-menu-action-delete";
    deleteButton.dataset.canvasAction = "delete";
    deleteButton.textContent = "Delete";
    panel.appendChild(deleteButton);

    const confirmPanel = document.createElement("div");
    confirmPanel.className = "comic-canvas-delete-confirm";
    confirmPanel.hidden = true;

    const confirmText = document.createElement("p");
    confirmText.className = "comic-canvas-delete-confirm-copy";
    confirmText.textContent = "Delete this canvas?";

    const confirmActions = document.createElement("div");
    confirmActions.className = "comic-canvas-delete-confirm-actions";

    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "comic-canvas-delete-confirm-btn is-cancel";
    cancelButton.dataset.canvasAction = "cancel-delete";
    cancelButton.textContent = "Cancel";

    const confirmDeleteButton = document.createElement("button");
    confirmDeleteButton.type = "button";
    confirmDeleteButton.className = "comic-canvas-delete-confirm-btn is-confirm";
    confirmDeleteButton.dataset.canvasAction = "confirm-delete";
    confirmDeleteButton.textContent = "Delete canvas";

    confirmActions.append(cancelButton, confirmDeleteButton);
    confirmPanel.append(confirmText, confirmActions);
    panel.appendChild(confirmPanel);

    const quickPromptPanel = document.createElement("div");
    quickPromptPanel.className = "comic-canvas-quick-prompt";
    quickPromptPanel.hidden = true;

    const quickPromptText = document.createElement("textarea");
    quickPromptText.className = "comic-canvas-quick-prompt-input";
    quickPromptText.dataset.canvasQuickPromptInput = "true";
    quickPromptText.rows = 4;
    quickPromptText.placeholder = "Describe only what to change in this picture.";

    const quickPromptActions = document.createElement("div");
    quickPromptActions.className = "comic-canvas-quick-prompt-actions";

    const quickPromptCancel = document.createElement("button");
    quickPromptCancel.type = "button";
    quickPromptCancel.className = "comic-canvas-delete-confirm-btn is-cancel";
    quickPromptCancel.dataset.canvasAction = "cancel-quick-prompt";
    quickPromptCancel.textContent = "Cancel";

    const quickPromptApply = document.createElement("button");
    quickPromptApply.type = "button";
    quickPromptApply.className = "comic-canvas-delete-confirm-btn is-confirm";
    quickPromptApply.dataset.canvasAction = "apply-quick-prompt";
    quickPromptApply.textContent = "Apply";

    quickPromptActions.append(quickPromptCancel, quickPromptApply);
    quickPromptPanel.append(quickPromptText, quickPromptActions);
    panel.appendChild(quickPromptPanel);

    const quickPromptReviewPanel = document.createElement("div");
    quickPromptReviewPanel.className = "comic-canvas-quick-prompt-review";
    quickPromptReviewPanel.hidden = true;

    const quickPromptReviewCopy = document.createElement("p");
    quickPromptReviewCopy.className = "comic-canvas-quick-prompt-review-copy";
    quickPromptReviewCopy.textContent = "Use this Quick Prompt result?";

    const quickPromptReviewActions = document.createElement("div");
    quickPromptReviewActions.className = "comic-canvas-quick-prompt-actions";

    const quickPromptReject = document.createElement("button");
    quickPromptReject.type = "button";
    quickPromptReject.className = "comic-canvas-delete-confirm-btn is-cancel";
    quickPromptReject.dataset.canvasAction = "reject-quick-prompt";
    quickPromptReject.textContent = "Reject";

    const quickPromptAccept = document.createElement("button");
    quickPromptAccept.type = "button";
    quickPromptAccept.className = "comic-canvas-delete-confirm-btn is-confirm";
    quickPromptAccept.dataset.canvasAction = "accept-quick-prompt";
    quickPromptAccept.textContent = "Accept";

    quickPromptReviewActions.append(quickPromptReject, quickPromptAccept);
    quickPromptReviewPanel.append(quickPromptReviewCopy, quickPromptReviewActions);
    panel.appendChild(quickPromptReviewPanel);

    details.append(summary, panel);
    return details;
  };

  const setDeleteConfirmVisibility = (menu, isVisible) => {
    if (!(menu instanceof HTMLElement)) return;
    const deleteButton = menu.querySelector('[data-canvas-action="delete"]');
    const confirmPanel = menu.querySelector(".comic-canvas-delete-confirm");
    if (!(confirmPanel instanceof HTMLElement) || !(deleteButton instanceof HTMLElement)) return;
    confirmPanel.hidden = !isVisible;
    deleteButton.hidden = isVisible;
  };

  const setQuickPromptVisibility = (menu, isVisible) => {
    if (!(menu instanceof HTMLElement)) return;
    const panel = menu.querySelector(".comic-canvas-quick-prompt");
    const input = menu.querySelector("[data-canvas-quick-prompt-input]");
    if (!(panel instanceof HTMLElement)) return;
    panel.hidden = !isVisible;
    if (isVisible && input instanceof HTMLTextAreaElement) {
      input.focus();
    }
  };

  const setQuickPromptReviewVisibility = (menu, isVisible) => {
    if (!(menu instanceof HTMLElement)) return;
    const panel = menu.querySelector(".comic-canvas-quick-prompt-review");
    if (!(panel instanceof HTMLElement)) return;
    panel.hidden = !isVisible;
  };

  const syncCanvasMenuLayering = () => {
    rootPanel.querySelectorAll(".is-canvas-menu-active").forEach((node) => {
      node.classList.remove("is-canvas-menu-active");
    });

    rootPanel.querySelectorAll(".comic-canvas-menu").forEach((menu) => {
      if (!(menu instanceof HTMLDetailsElement) || !menu.open) {
        setDeleteConfirmVisibility(menu, false);
        setQuickPromptVisibility(menu, false);
        setQuickPromptReviewVisibility(menu, false);
        return;
      }

      let panel = menu.closest("[data-canvas-panel]");
      while (panel instanceof HTMLElement) {
        panel.classList.add("is-canvas-menu-active");
        panel = panel.parentElement?.closest?.("[data-canvas-panel]") || null;
      }
    });
  };

  const createSurface = () => {
    const surface = document.createElement("div");
    surface.className = "comic-canvas-surface";
    return surface;
  };

  const nextSpeechBubbleId = () => {
    speechBubbleSequence += 1;
    return `speech-${speechBubbleSequence}`;
  };

  const clampPercent = (value, min, max) => Math.max(min, Math.min(max, Number(value) || 0));

  const getBubbleBorderRadius = (bubbleState = {}) => clampPercent(bubbleState.border_radius ?? 50, 0, 50);

  const getBubbleFontSize = (bubbleState = {}) => clampPercent(bubbleState.font_size ?? 16, 8, 36);

  const getDefaultBubblePointer = (bubbleState = {}) => ({
    pointer_x: clampPercent(
      bubbleState.pointer_x ?? (Number(bubbleState.x ?? 14) + Number(bubbleState.width ?? 34) * 0.68),
      0,
      100
    ),
    pointer_y: clampPercent(
      bubbleState.pointer_y ?? (Number(bubbleState.y ?? 12) + Number(bubbleState.height ?? 18) + 8),
      0,
      100
    ),
  });

  const updateSpeechBubblePointer = (bubble) => {
    if (!(bubble instanceof HTMLElement)) return;
    const panel = bubble.closest("[data-canvas-panel]");
    const pointer = bubble.querySelector(".comic-speech-bubble-pointer");
    if (!(panel instanceof HTMLElement) || !(pointer instanceof HTMLElement)) return;

    const panelRect = panel.getBoundingClientRect();
    if (!panelRect.width || !panelRect.height) return;

    const left = (Number.parseFloat(bubble.style.left) || 0) / 100 * panelRect.width;
    const top = (Number.parseFloat(bubble.style.top) || 0) / 100 * panelRect.height;
    const width = (Number.parseFloat(bubble.style.width) || 34) / 100 * panelRect.width;
    const height = (Number.parseFloat(bubble.style.height) || 18) / 100 * panelRect.height;
    const targetX = (Number(bubble.dataset.pointerX) || 0) / 100 * panelRect.width;
    const targetY = (Number(bubble.dataset.pointerY) || 0) / 100 * panelRect.height;
    const anchorX = width / 2;
    const anchorY = height / 2;
    const dx = targetX - (left + anchorX);
    const dy = targetY - (top + anchorY);
    const length = Math.max(18, Math.hypot(dx, dy));
    const angle = Math.atan2(dy, dx) * (180 / Math.PI);

    pointer.style.left = `${anchorX}px`;
    pointer.style.top = `${anchorY}px`;
    pointer.style.width = `${length}px`;
    pointer.style.transform = `rotate(${angle}deg)`;
  };

  const updateAllSpeechBubblePointers = () => {
    rootPanel.querySelectorAll(".comic-speech-bubble").forEach((bubble) => updateSpeechBubblePointer(bubble));
  };

  const createSpeechBubble = (bubbleState = {}) => {
    const bubble = document.createElement("div");
    bubble.className = "comic-speech-bubble";
    bubble.dataset.speechBubble = "true";
    bubble.dataset.bubbleId = String(bubbleState.id || nextSpeechBubbleId());
    const idMatch = bubble.dataset.bubbleId.match(/^speech-(\d+)$/);
    if (idMatch) {
      speechBubbleSequence = Math.max(speechBubbleSequence, Number(idMatch[1]) || 0);
    }
    bubble.dataset.flipped = bubbleState.flipped ? "true" : "false";
    bubble.style.left = `${clampPercent(bubbleState.x ?? 14, 0, 85)}%`;
    bubble.style.top = `${clampPercent(bubbleState.y ?? 12, 0, 85)}%`;
    bubble.style.width = `${clampPercent(bubbleState.width ?? 34, 12, 90)}%`;
    bubble.style.height = `${clampPercent(bubbleState.height ?? 18, 10, 80)}%`;
    bubble.style.borderRadius = `${getBubbleBorderRadius(bubbleState)}%`;
    const pointerTarget = getDefaultBubblePointer(bubbleState);
    bubble.dataset.pointerX = String(pointerTarget.pointer_x);
    bubble.dataset.pointerY = String(pointerTarget.pointer_y);

    const toolbar = document.createElement("div");
    toolbar.className = "comic-speech-bubble-toolbar";

    const moveHandle = document.createElement("button");
    moveHandle.type = "button";
    moveHandle.className = "comic-speech-bubble-tool comic-speech-bubble-move";
    moveHandle.dataset.bubbleAction = "move";
    moveHandle.textContent = "Move";

    const flipButton = document.createElement("button");
    flipButton.type = "button";
    flipButton.className = "comic-speech-bubble-tool";
    flipButton.dataset.bubbleAction = "flip";
    flipButton.textContent = "Flip";

    const pointerButton = document.createElement("button");
    pointerButton.type = "button";
    pointerButton.className = "comic-speech-bubble-tool comic-speech-bubble-pointer-tool";
    pointerButton.dataset.bubbleAction = "pointer";
    pointerButton.textContent = "Pointer";

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "comic-speech-bubble-tool is-delete";
    deleteButton.dataset.bubbleAction = "delete";
    deleteButton.textContent = "Delete";

    const radiusLabel = document.createElement("label");
    radiusLabel.className = "comic-speech-bubble-slider";

    const radiusText = document.createElement("span");
    radiusText.textContent = "Radius";

    const radiusInput = document.createElement("input");
    radiusInput.type = "range";
    radiusInput.min = "0";
    radiusInput.max = "50";
    radiusInput.step = "1";
    radiusInput.value = String(getBubbleBorderRadius(bubbleState));
    radiusInput.dataset.bubbleRadius = "true";
    radiusInput.addEventListener("input", () => {
      bubble.style.borderRadius = `${getBubbleBorderRadius({ border_radius: radiusInput.value })}%`;
      syncLayoutInput();
    });

    radiusLabel.append(radiusText, radiusInput);

    const fontSizeLabel = document.createElement("label");
    fontSizeLabel.className = "comic-speech-bubble-slider";

    const fontSizeText = document.createElement("span");
    fontSizeText.textContent = "Text";

    const fontSizeInput = document.createElement("input");
    fontSizeInput.type = "range";
    fontSizeInput.min = "8";
    fontSizeInput.max = "36";
    fontSizeInput.step = "1";
    fontSizeInput.value = String(getBubbleFontSize(bubbleState));
    fontSizeInput.dataset.bubbleFontSize = "true";

    fontSizeLabel.append(fontSizeText, fontSizeInput);
    toolbar.append(moveHandle, flipButton, pointerButton, radiusLabel, fontSizeLabel, deleteButton);

    const text = document.createElement("div");
    text.className = "comic-speech-bubble-text";
    text.contentEditable = "true";
    text.dataset.bubbleText = "true";
    text.setAttribute("role", "textbox");
    text.setAttribute("aria-label", "Speech bubble text");
    text.textContent = String(bubbleState.text || "Speech bubble");
    text.style.fontSize = `${getBubbleFontSize(bubbleState)}px`;
    text.addEventListener("input", syncLayoutInput);
    fontSizeInput.addEventListener("input", () => {
      text.style.fontSize = `${getBubbleFontSize({ font_size: fontSizeInput.value })}px`;
      syncLayoutInput();
    });

    const resize = document.createElement("button");
    resize.type = "button";
    resize.className = "comic-speech-bubble-resize";
    resize.dataset.bubbleAction = "resize";
    resize.setAttribute("aria-label", "Resize speech bubble");

    const pointer = document.createElement("div");
    pointer.className = "comic-speech-bubble-pointer";

    bubble.append(pointer, toolbar, text, resize);
    requestAnimationFrame(() => updateSpeechBubblePointer(bubble));
    return bubble;
  };

  const getPanelSpeechBubbles = (panel) =>
    Array.from(panel.querySelectorAll(":scope > .comic-speech-bubble")).filter((bubble) => bubble instanceof HTMLElement);

  const serializeSpeechBubbles = (panel) =>
    getPanelSpeechBubbles(panel).map((bubble) => {
      const text = bubble.querySelector("[data-bubble-text]");
      return {
        id: bubble.dataset.bubbleId || "",
        text: text instanceof HTMLElement ? text.textContent.trim() : "",
        x: Number.parseFloat(bubble.style.left) || 0,
        y: Number.parseFloat(bubble.style.top) || 0,
        width: Number.parseFloat(bubble.style.width) || 34,
        height: Number.parseFloat(bubble.style.height) || 18,
        border_radius: Number.parseFloat(bubble.style.borderRadius) || 0,
        font_size: Number.parseFloat(text instanceof HTMLElement ? text.style.fontSize : "") || 16,
        pointer_x: Number(bubble.dataset.pointerX) || 0,
        pointer_y: Number(bubble.dataset.pointerY) || 0,
        flipped: bubble.dataset.flipped === "true",
      };
    });

  const addSpeechBubble = (panel, bubbleState = {}) => {
    if (!(panel instanceof HTMLElement)) return null;
    if (Array.from(panel.children).some((child) => child.classList.contains("comic-canvas-split"))) return null;
    const bubble = createSpeechBubble(bubbleState);
    panel.appendChild(bubble);
    syncLayoutInput();
    return bubble;
  };

  const setCanvasImage = (panel, imageUrl) => {
    if (!(panel instanceof HTMLElement) || !imageUrl) return;
    const surface = panel.querySelector(":scope > .comic-canvas-surface");
    if (!(surface instanceof HTMLElement)) return;
    surface.replaceChildren();
    const image = document.createElement("img");
    image.className = "comic-canvas-image";
    image.src = imageUrl;
    image.alt = `Generated image for ${ensureCanvasKey(panel) || "canvas"}`;
    surface.appendChild(image);
  };

  const loadCanvasImages = () => {
    const dataScript = document.getElementById("comic-canvas-image-data");
    if (!dataScript) return;
    let imageMap = {};
    try {
      imageMap = JSON.parse(dataScript.textContent || "{}");
    } catch (_error) {
      imageMap = {};
    }
    if (!imageMap || typeof imageMap !== "object") return;
    rootPanel.querySelectorAll("[data-canvas-panel]").forEach((panel) => {
      if (!(panel instanceof HTMLElement)) return;
      const imageUrl = imageMap[ensureCanvasKey(panel)];
      if (imageUrl) setCanvasImage(panel, imageUrl);
    });
  };

  const isChildCanvasPanel = (panel) =>
    panel instanceof HTMLElement &&
    panel !== rootPanel &&
    panel.parentElement instanceof HTMLElement &&
    panel.parentElement.classList.contains("comic-canvas-split");

  const getEventCanvasPanel = (event) => {
    const panel = event.target?.closest?.("[data-canvas-panel]");
    return panel instanceof HTMLElement ? panel : null;
  };

  const isInteractiveCanvasControl = (target) =>
    Boolean(
      target?.closest?.(
        ".comic-canvas-menu, .comic-canvas-divider, .comic-canvas-junction, .comic-speech-bubble, button, a, input, select, textarea"
      )
    );

  const clearCanvasSwapState = () => {
    rootPanel.querySelectorAll(".is-canvas-swap-target").forEach((node) => {
      node.classList.remove("is-canvas-swap-target");
    });
  };

  const isValidCanvasSwapTarget = (targetPanel) =>
    isChildCanvasPanel(draggedCanvasPanel) &&
    isChildCanvasPanel(targetPanel) &&
    targetPanel !== draggedCanvasPanel &&
    !targetPanel.contains(draggedCanvasPanel) &&
    !draggedCanvasPanel.contains(targetPanel);

  const syncCanvasPanelDraggability = () => {
    rootPanel.querySelectorAll("[data-canvas-panel]").forEach((panel) => {
      if (!(panel instanceof HTMLElement)) return;
      const canDrag = isChildCanvasPanel(panel);
      panel.draggable = canDrag;
      if (canDrag) {
        panel.dataset.canvasSwapPanel = "true";
        panel.title = "Drag to swap canvas";
      } else {
        panel.removeAttribute("draggable");
        panel.removeAttribute("data-canvas-swap-panel");
        panel.removeAttribute("title");
      }
    });
  };

  const swapCanvasPanels = (firstPanel, secondPanel) => {
    if (!isValidCanvasSwapTarget(secondPanel)) return false;

    const firstKey = ensureCanvasKey(firstPanel);
    const secondKey = ensureCanvasKey(secondPanel);
    const firstNodes = Array.from(firstPanel.childNodes);
    const secondNodes = Array.from(secondPanel.childNodes);

    firstPanel.replaceChildren(...secondNodes);
    secondPanel.replaceChildren(...firstNodes);
    firstPanel.dataset.canvasKey = secondKey;
    secondPanel.dataset.canvasKey = firstKey;
    firstPanel.dataset.canvasKeyRegistered = "true";
    secondPanel.dataset.canvasKeyRegistered = "true";

    syncLayoutInput();
    renderJunctionHandles();
    syncCanvasMenuLayering();
    syncCanvasPanelDraggability();
    return true;
  };

  const generateCanvasImage = async (panel, action, menu) => {
    const ui = window.AppUI;
    if (!ui || !(panel instanceof HTMLElement)) return;
    const canvasKey = ensureCanvasKey(panel);
    const url = buildCanvasGenerateUrl(canvasKey);
    if (!url) return;

    const originalText = action.textContent;
    action.textContent = "Generating...";
    action.setAttribute("aria-busy", "true");
    action.disabled = true;
    try {
      const result = await ui.postFormUrlEncoded({
        url,
        params: new URLSearchParams(),
        csrfToken: ui.getCsrfToken(),
        failureLabel: "Generate failed",
      });
      if (window.AIBillingGuard?.handleBillingResponse({ status: result.status }, result.data)) return;
      if (!result.ok) {
        ui.showMessage(result.error, "error");
        return;
      }
      const imageUrl = result.data?.image_url || "";
      if (!imageUrl) {
        ui.showMessage("No image returned.", "warning");
        return;
      }
      setCanvasImage(panel, imageUrl);
      ui.showMessage("Canvas image generated.", "success");
      if (menu instanceof HTMLDetailsElement) menu.open = false;
    } finally {
      action.textContent = originalText;
      action.removeAttribute("aria-busy");
      action.disabled = false;
    }
  };

  const loadImageForCanvas = (src) =>
    new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = reject;
      image.src = src;
    });

  const wrapCanvasText = (context, text, maxWidth) => {
    const words = String(text || "").toUpperCase().split(/\s+/).filter(Boolean);
    const lines = [];
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (context.measureText(next).width <= maxWidth || !current) {
        current = next;
      } else {
        lines.push(current);
        current = word;
      }
    }
    if (current) lines.push(current);
    return lines;
  };

  const drawSpeechBubbleOnCanvas = (context, bubble, canvasSize) => {
    const x = (Number.parseFloat(bubble.style.left) || 0) / 100 * canvasSize;
    const y = (Number.parseFloat(bubble.style.top) || 0) / 100 * canvasSize;
    const width = (Number.parseFloat(bubble.style.width) || 34) / 100 * canvasSize;
    const height = (Number.parseFloat(bubble.style.height) || 18) / 100 * canvasSize;
    const centerX = x + width / 2;
    const centerY = y + height / 2;
    const pointerX = (Number(bubble.dataset.pointerX) || 0) / 100 * canvasSize;
    const pointerY = (Number(bubble.dataset.pointerY) || 0) / 100 * canvasSize;
    const radiusPercent = Number.parseFloat(bubble.style.borderRadius) || 50;
    const radius = Math.min(width, height) * (radiusPercent / 100);

    context.save();
    context.fillStyle = "rgba(255, 255, 255, 0.96)";
    context.beginPath();
    context.moveTo(centerX, centerY);
    context.lineTo(pointerX, pointerY);
    context.lineTo(centerX + Math.max(12, width * 0.08), centerY + Math.max(8, height * 0.08));
    context.closePath();
    context.fill();

    context.beginPath();
    context.roundRect(x, y, width, height, radius);
    context.fill();

    const text = bubble.querySelector("[data-bubble-text]");
    const textValue = text instanceof HTMLElement ? text.textContent || "" : "";
    const fontSize = Number.parseFloat(text instanceof HTMLElement ? text.style.fontSize : "") || 16;
    context.fillStyle = "rgba(17, 24, 39, 0.96)";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.font = `900 ${fontSize * (canvasSize / 720)}px "Comic Sans MS", "Comic Neue", "Trebuchet MS", Arial, sans-serif`;
    const lines = wrapCanvasText(context, textValue, width * 0.78).slice(0, 5);
    const lineHeight = fontSize * (canvasSize / 720) * 1.18;
    const startY = centerY - ((lines.length - 1) * lineHeight) / 2;
    lines.forEach((line, index) => context.fillText(line, centerX, startY + index * lineHeight));
    context.restore();
  };

  const createCanvasReferenceImage = async (panel) => {
    if (!(panel instanceof HTMLElement)) return "";
    const image = panel.querySelector(":scope > .comic-canvas-surface .comic-canvas-image");
    if (!(image instanceof HTMLImageElement) || !image.src) return "";
    const loadedImage = await loadImageForCanvas(image.src);
    const canvasSize = 1024;
    const canvas = document.createElement("canvas");
    canvas.width = canvasSize;
    canvas.height = canvasSize;
    const context = canvas.getContext("2d");
    if (!context) return "";
    context.drawImage(loadedImage, 0, 0, canvasSize, canvasSize);
    return canvas.toDataURL("image/png");
  };

  const quickPromptCanvasImage = async (panel, action, menu) => {
    const ui = window.AppUI;
    if (!ui || !(panel instanceof HTMLElement)) return;
    const input = menu?.querySelector?.("[data-canvas-quick-prompt-input]");
    const prompt = input instanceof HTMLTextAreaElement ? input.value.trim() : "";
    if (!prompt) {
      ui.showMessage("Write what to change first.", "warning");
      return;
    }
    const canvasKey = ensureCanvasKey(panel);
    const url = buildCanvasQuickPromptUrl(canvasKey);
    if (!url) return;

    const originalText = action.textContent;
    action.textContent = "Applying...";
    action.setAttribute("aria-busy", "true");
    action.disabled = true;
    try {
      const image = panel.querySelector(":scope > .comic-canvas-surface .comic-canvas-image");
      if (!(image instanceof HTMLImageElement) || !image.src) {
        ui.showMessage("Generate this canvas image before using Quick Prompt.", "warning");
        return;
      }
      const params = new URLSearchParams();
      params.set("prompt", prompt);
      const result = await ui.postFormUrlEncoded({
        url,
        params,
        csrfToken: ui.getCsrfToken(),
        failureLabel: "Quick Prompt failed",
      });
      if (window.AIBillingGuard?.handleBillingResponse({ status: result.status }, result.data)) return;
      if (!result.ok) {
        ui.showMessage(result.error, "error");
        return;
      }
      const imageUrl = result.data?.image_url || "";
      const pendingToken = result.data?.pending_token || "";
      if (!imageUrl) {
        ui.showMessage("No image returned.", "warning");
        return;
      }
      if (!pendingToken) {
        ui.showMessage("No Quick Prompt preview token returned.", "warning");
        return;
      }
      const previousImage = image instanceof HTMLImageElement ? image.src : "";
      setCanvasImage(panel, imageUrl);
      pendingQuickPrompts.set(canvasKey, { previousImage, previewImage: imageUrl, pendingToken });
      if (input instanceof HTMLTextAreaElement) input.value = "";
      setQuickPromptVisibility(menu, false);
      setQuickPromptReviewVisibility(menu, true);
      if (menu instanceof HTMLDetailsElement) menu.open = true;
      ui.showMessage("Quick Prompt preview ready.", "success");
    } finally {
      action.textContent = originalText;
      action.removeAttribute("aria-busy");
      action.disabled = false;
    }
  };

  const resolveQuickPromptPreview = async (panel, action, menu, shouldAccept) => {
    const ui = window.AppUI;
    if (!ui || !(panel instanceof HTMLElement)) return;
    const canvasKey = ensureCanvasKey(panel);
    const pending = pendingQuickPrompts.get(canvasKey);
    if (!pending?.pendingToken) {
      ui.showMessage("No Quick Prompt preview is waiting.", "warning");
      setQuickPromptReviewVisibility(menu, false);
      return;
    }

    const url = shouldAccept ? buildCanvasQuickPromptAcceptUrl(canvasKey) : buildCanvasQuickPromptRejectUrl(canvasKey);
    if (!url) return;

    const originalText = action.textContent;
    action.textContent = shouldAccept ? "Accepting..." : "Rejecting...";
    action.disabled = true;
    try {
      const params = new URLSearchParams();
      params.set("pending_token", pending.pendingToken);
      const result = await ui.postFormUrlEncoded({
        url,
        params,
        csrfToken: ui.getCsrfToken(),
        failureLabel: shouldAccept ? "Accept failed" : "Reject failed",
      });
      if (!result.ok) {
        ui.showMessage(result.error, "error");
        return;
      }

      if (shouldAccept) {
        const imageUrl = result.data?.image_url || pending.previewImage;
        setCanvasImage(panel, imageUrl);
        ui.showMessage("Quick Prompt accepted.", "success");
      } else {
        setCanvasImage(panel, pending.previousImage || result.data?.image_url || "");
        ui.showMessage("Quick Prompt rejected.", "info");
      }
      pendingQuickPrompts.delete(canvasKey);
      setQuickPromptReviewVisibility(menu, false);
      if (menu instanceof HTMLDetailsElement) menu.open = false;
    } finally {
      action.textContent = originalText;
      action.disabled = false;
    }
  };

  const setPanelRatio = (panel, ratio) => {
    const clampedRatio = Math.max(0.1, Math.min(0.9, Number(ratio) || 0.5));
    panel.style.flexBasis = `${clampedRatio * 100}%`;
    panel.style.flexGrow = "0";
    panel.style.flexShrink = "0";
  };

  const ensureSplitId = (split) => {
    if (!(split instanceof HTMLElement)) return "";
    if (!split.dataset.splitId) {
      splitIdSequence += 1;
      split.dataset.splitId = `split-${splitIdSequence}`;
    }
    return split.dataset.splitId;
  };

  const createDivider = () => {
    const divider = document.createElement("div");
    divider.className = "comic-canvas-divider";
    divider.setAttribute("data-canvas-divider", "");
    return divider;
  };

  const createCanvasPanel = () => {
    const panel = document.createElement("div");
    panel.className = "comic-canvas-panel";
    panel.setAttribute("data-canvas-panel", "");
    ensureCanvasKey(panel);
    panel.append(createMenu(panel), createSurface());
    return panel;
  };

  const parseRatio = (panel) => {
    const basis = panel.style.flexBasis || "";
    const ratio = basis.endsWith("%") ? Number.parseFloat(basis) / 100 : Number.NaN;
    return Number.isFinite(ratio) ? ratio : 0.5;
  };

  const getChildPanels = (split) =>
    Array.from(split.children).filter((child) => child.classList.contains("comic-canvas-panel"));

  const getSplitById = (splitId) =>
    splitId ? document.querySelector(`.comic-canvas-split[data-split-id="${splitId}"]`) : null;

  const getSplitInfo = (split) => {
    if (!(split instanceof HTMLElement)) return null;
    const panels = getChildPanels(split);
    if (panels.length !== 2) return null;
    return {
      split,
      panels,
      direction: split.dataset.splitDirection || "vertical",
    };
  };

  const updatePanelRatioByPointer = (split, clientX, clientY) => {
    const info = getSplitInfo(split);
    if (!info) return false;

    const [firstPanel, secondPanel] = info.panels;
    const divider = split.querySelector(":scope > [data-canvas-divider]");
    if (!(divider instanceof HTMLElement)) return false;

    const dividerSize = info.direction === "horizontal" ? divider.offsetHeight : divider.offsetWidth;
    const axis = getAxisMetrics(split);
    const availableSize = axis.size - dividerSize;
    if (availableSize <= MIN_PANEL_SIZE * 2) return false;

    const pointerOffset = info.direction === "horizontal" ? clientY - axis.start : clientX - axis.start;
    const rawFirstSize = pointerOffset - dividerSize / 2;
    const nextFirstSize = Math.max(MIN_PANEL_SIZE, Math.min(availableSize - MIN_PANEL_SIZE, rawFirstSize));
    const firstRatio = nextFirstSize / availableSize;
    setPanelRatio(firstPanel, firstRatio);
    setPanelRatio(secondPanel, 1 - firstRatio);
    return true;
  };

  const renderJunctionHandles = () => {
    rootPanel.querySelectorAll(".comic-canvas-junction").forEach((node) => node.remove());

    const visitPanel = (panel, parentSplit = null, childIndex = -1) => {
      const nestedSplit = Array.from(panel.children).find((child) => child.classList.contains("comic-canvas-split"));
      if (!(nestedSplit instanceof HTMLElement)) {
        return;
      }

      ensureSplitId(nestedSplit);

      if (parentSplit instanceof HTMLElement) {
        ensureSplitId(parentSplit);
        const parentDirection = parentSplit.dataset.splitDirection || "";
        const childDirection = nestedSplit.dataset.splitDirection || "";
        const isPerpendicular =
          (parentDirection === "vertical" && childDirection === "horizontal") ||
          (parentDirection === "horizontal" && childDirection === "vertical");

        if (isPerpendicular) {
          const junction = document.createElement("button");
          junction.type = "button";
          junction.className = "comic-canvas-junction";
          junction.dataset.parentSplitId = parentSplit.dataset.splitId || "";
          junction.dataset.childSplitId = nestedSplit.dataset.splitId || "";
          junction.dataset.childIndex = String(childIndex);
          junction.dataset.junctionAxis = `${parentDirection}-${childDirection}`;

          const nestedPanels = getChildPanels(nestedSplit);
          const ratio = parseRatio(nestedPanels[0] || panel);

          if (parentDirection === "vertical") {
            junction.style.top = `${ratio * 100}%`;
            junction.style.left = childIndex === 0 ? "100%" : "0";
          } else {
            junction.style.left = `${ratio * 100}%`;
            junction.style.top = childIndex === 0 ? "100%" : "0";
          }

          panel.appendChild(junction);
        }
      }

      getChildPanels(nestedSplit).forEach((childPanel, index) => visitPanel(childPanel, nestedSplit, index));
    };

    visitPanel(rootPanel);
  };

  const syncLayoutInput = () => {
    const serializePanel = (panel) => {
      const split = Array.from(panel.children).find((child) => child.classList.contains("comic-canvas-split"));
      if (!(split instanceof HTMLElement)) {
        const serializedPanel = { type: "panel", canvas_key: ensureCanvasKey(panel) };
        const bubbles = serializeSpeechBubbles(panel);
        if (bubbles.length) {
          serializedPanel.speech_bubbles = bubbles;
        }
        return serializedPanel;
      }

      const childPanels = getChildPanels(split);
      const serializedSplit = {
        type: "split",
        canvas_key: ensureCanvasKey(panel),
        direction: split.dataset.splitDirection || "vertical",
        ratio: parseRatio(childPanels[0] || panel),
        children: childPanels.map((childPanel) => serializePanel(childPanel)),
      };
      const bubbles = serializeSpeechBubbles(panel);
      if (bubbles.length) {
        serializedSplit.speech_bubbles = bubbles;
      }
      return serializedSplit;
    };

    layoutInput.value = JSON.stringify(serializePanel(rootPanel));
  };

  const splitPanel = (panel, direction, ratio = 0.5) => {
    if (!(panel instanceof HTMLElement) || !direction) return;

    ensureCanvasKey(panel);
    const firstPanel = createCanvasPanel();
    const secondPanel = createCanvasPanel();
    const split = document.createElement("div");
    split.className = "comic-canvas-split";
    split.dataset.splitDirection = direction;
    ensureSplitId(split);
    split.append(firstPanel, createDivider(), secondPanel);
    setPanelRatio(firstPanel, ratio);
    setPanelRatio(secondPanel, 1 - ratio);

    panel.replaceChildren(split);
    syncLayoutInput();
    renderJunctionHandles();
    syncCanvasPanelDraggability();
  };

  const deletePanel = (panel) => {
    if (!(panel instanceof HTMLElement)) return;

    const split = panel.parentElement;
    if (!(split instanceof HTMLElement) || !split.classList.contains("comic-canvas-split")) return;

    const parentPanel = split.parentElement;
    if (!(parentPanel instanceof HTMLElement) || !parentPanel.matches("[data-canvas-panel]")) return;

    const siblingPanel = getChildPanels(split).find((child) => child !== panel);
    if (!(siblingPanel instanceof HTMLElement)) return;

    parentPanel.replaceChildren(...Array.from(siblingPanel.childNodes));
    syncLayoutInput();
    renderJunctionHandles();
    syncCanvasPanelDraggability();
  };

  const resetCanvas = () => {
    rootPanel.replaceChildren(createMenu(rootPanel), createSurface());
    rootPanel.style.flexBasis = "";
    rootPanel.style.flexGrow = "";
    rootPanel.style.flexShrink = "";
    rootPanel.dataset.canvasKey = "root";
    syncLayoutInput();
    renderJunctionHandles();
    syncCanvasMenuLayering();
    syncCanvasPanelDraggability();
  };

  const getAxisMetrics = (split) => {
    const direction = split.dataset.splitDirection || "";
    if (direction === "horizontal") {
      return {
        size: split.clientHeight,
        start: split.getBoundingClientRect().top,
      };
    }
    return {
      size: split.clientWidth,
      start: split.getBoundingClientRect().left,
    };
  };

  const startResize = (divider, pointerEvent) => {
    const split = divider.closest(".comic-canvas-split");
    if (!(split instanceof HTMLElement)) return;

    const panels = getChildPanels(split);
    if (panels.length !== 2) return;

    const [firstPanel, secondPanel] = panels;
    const direction = split.dataset.splitDirection || "";
    const dividerSize = direction === "horizontal" ? divider.offsetHeight : divider.offsetWidth;
    const axis = getAxisMetrics(split);
    const availableSize = axis.size - dividerSize;
    if (availableSize <= MIN_PANEL_SIZE * 2) return;

    divider.classList.add("is-dragging");
    document.body.style.userSelect = "none";

    const updateFromPointer = (clientX, clientY) => {
      const pointerOffset = direction === "horizontal" ? clientY - axis.start : clientX - axis.start;
      const rawFirstSize = pointerOffset - dividerSize / 2;
      const nextFirstSize = Math.max(MIN_PANEL_SIZE, Math.min(availableSize - MIN_PANEL_SIZE, rawFirstSize));
      const firstRatio = nextFirstSize / availableSize;
      setPanelRatio(firstPanel, firstRatio);
      setPanelRatio(secondPanel, 1 - firstRatio);
      syncLayoutInput();
      renderJunctionHandles();
      updateAllSpeechBubblePointers();
    };

    updateFromPointer(pointerEvent.clientX, pointerEvent.clientY);

    const handlePointerMove = (moveEvent) => {
      updateFromPointer(moveEvent.clientX, moveEvent.clientY);
    };

    const stopResize = () => {
      divider.classList.remove("is-dragging");
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  };

  const hydratePanel = (panel, state) => {
    if (!(panel instanceof HTMLElement)) return;

    const normalizedState = state && typeof state === "object" ? state : { type: "panel" };
    ensureCanvasKey(panel, normalizedState.canvas_key || "");
    if (normalizedState.type !== "split") {
      panel.replaceChildren(createMenu(panel), createSurface());
      const bubbles = Array.isArray(normalizedState.speech_bubbles) ? normalizedState.speech_bubbles : [];
      bubbles.forEach((bubbleState) => addSpeechBubble(panel, bubbleState));
      return;
    }

    const firstPanel = createCanvasPanel();
    const secondPanel = createCanvasPanel();
    const split = document.createElement("div");
    split.className = "comic-canvas-split";
    split.dataset.splitDirection = normalizedState.direction === "horizontal" ? "horizontal" : "vertical";
    ensureSplitId(split);
    split.append(firstPanel, createDivider(), secondPanel);
    setPanelRatio(firstPanel, normalizedState.ratio);
    setPanelRatio(secondPanel, 1 - parseRatio(firstPanel));

    panel.replaceChildren(split);

    const children = Array.isArray(normalizedState.children) ? normalizedState.children : [];
    hydratePanel(firstPanel, children[0]);
    hydratePanel(secondPanel, children[1]);
  };

  const loadSavedLayout = () => {
    const raw = layoutInput.value.trim();
    if (!raw) {
      syncLayoutInput();
      return;
    }

    try {
      hydratePanel(rootPanel, JSON.parse(raw));
    } catch (_error) {
      hydratePanel(rootPanel, { type: "panel" });
    }

    syncLayoutInput();
    renderJunctionHandles();
    syncCanvasPanelDraggability();
  };

  const startJunctionResize = (junction, pointerEvent) => {
    const parentSplit = getSplitById(junction.dataset.parentSplitId || "");
    const childSplit = getSplitById(junction.dataset.childSplitId || "");
    if (!(parentSplit instanceof HTMLElement) || !(childSplit instanceof HTMLElement)) return;

    junction.classList.add("is-dragging");
    document.body.style.userSelect = "none";

    const updateFromPointer = (clientX, clientY) => {
      const parentChanged = updatePanelRatioByPointer(parentSplit, clientX, clientY);
      const childChanged = updatePanelRatioByPointer(childSplit, clientX, clientY);
      if (parentChanged || childChanged) {
        syncLayoutInput();
        renderJunctionHandles();
        updateAllSpeechBubblePointers();
      }
    };

    updateFromPointer(pointerEvent.clientX, pointerEvent.clientY);

    const handlePointerMove = (moveEvent) => {
      updateFromPointer(moveEvent.clientX, moveEvent.clientY);
    };

    const stopResize = () => {
      junction.classList.remove("is-dragging");
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  };

  const startBubblePointerEdit = (bubble, pointerEvent, mode) => {
    const panel = bubble.closest("[data-canvas-panel]");
    if (!(panel instanceof HTMLElement)) return;
    const panelRect = panel.getBoundingClientRect();
    if (!panelRect.width || !panelRect.height) return;

    const startX = pointerEvent.clientX;
    const startY = pointerEvent.clientY;
    const startLeft = (Number.parseFloat(bubble.style.left) || 0) / 100 * panelRect.width;
    const startTop = (Number.parseFloat(bubble.style.top) || 0) / 100 * panelRect.height;
    const startWidth = (Number.parseFloat(bubble.style.width) || 34) / 100 * panelRect.width;
    const startHeight = (Number.parseFloat(bubble.style.height) || 18) / 100 * panelRect.height;

    bubble.classList.add(
      mode === "resize" ? "is-resizing" : mode === "pointer" || mode === "pointer-anchor" ? "is-pointing" : "is-moving"
    );
    document.body.style.userSelect = "none";

    const updateFromPointer = (clientX, clientY) => {
      if (mode === "pointer") {
        bubble.dataset.pointerX = String(clampPercent(((clientX - panelRect.left) / panelRect.width) * 100, 0, 100));
        bubble.dataset.pointerY = String(clampPercent(((clientY - panelRect.top) / panelRect.height) * 100, 0, 100));
        updateSpeechBubblePointer(bubble);
        syncLayoutInput();
        return;
      }

      const dx = clientX - startX;
      const dy = clientY - startY;
      if (mode === "resize") {
        const nextWidth = Math.max(MIN_BUBBLE_SIZE, Math.min(panelRect.width - startLeft, startWidth + dx));
        const nextHeight = Math.max(MIN_BUBBLE_SIZE, Math.min(panelRect.height - startTop, startHeight + dy));
        bubble.style.width = `${(nextWidth / panelRect.width) * 100}%`;
        bubble.style.height = `${(nextHeight / panelRect.height) * 100}%`;
      } else {
        const nextLeft = Math.max(0, Math.min(panelRect.width - startWidth, startLeft + dx));
        const nextTop = Math.max(0, Math.min(panelRect.height - startHeight, startTop + dy));
        bubble.style.left = `${(nextLeft / panelRect.width) * 100}%`;
        bubble.style.top = `${(nextTop / panelRect.height) * 100}%`;
      }
      updateSpeechBubblePointer(bubble);
      syncLayoutInput();
    };

    const handlePointerMove = (moveEvent) => {
      updateFromPointer(moveEvent.clientX, moveEvent.clientY);
    };

    const stopPointerEdit = () => {
      bubble.classList.remove("is-moving", "is-resizing", "is-pointing");
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopPointerEdit);
      window.removeEventListener("pointercancel", stopPointerEdit);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopPointerEdit);
    window.addEventListener("pointercancel", stopPointerEdit);
  };

  document.addEventListener("click", (event) => {
    const action = event.target.closest("[data-bubble-action]");
    if (!(action instanceof HTMLElement) || !rootPanel.contains(action)) return;
    const bubble = action.closest(".comic-speech-bubble");
    if (!(bubble instanceof HTMLElement)) return;

    const actionName = action.dataset.bubbleAction || "";
    if (actionName === "flip") {
      bubble.dataset.flipped = bubble.dataset.flipped === "true" ? "false" : "true";
      syncLayoutInput();
      return;
    }
    if (actionName === "delete") {
      bubble.remove();
      syncLayoutInput();
    }
  });

  document.addEventListener("click", (event) => {
    const action = event.target.closest(".comic-canvas-menu-action, .comic-canvas-delete-confirm-btn");
    if (!(action instanceof HTMLElement)) return;
    if (action.tagName === "A") return;

    const panel = action.closest("[data-canvas-panel]");
    const menu = action.closest(".comic-canvas-menu");
    if (!(panel instanceof HTMLElement)) return;

    if ((action.dataset.canvasAction || "") === "delete") {
      setDeleteConfirmVisibility(menu, true);
      return;
    }

    if ((action.dataset.canvasAction || "") === "show-quick-prompt") {
      setQuickPromptVisibility(menu, true);
      return;
    }

    if ((action.dataset.canvasAction || "") === "cancel-quick-prompt") {
      setQuickPromptVisibility(menu, false);
      return;
    }

    if ((action.dataset.canvasAction || "") === "apply-quick-prompt") {
      quickPromptCanvasImage(panel, action, menu);
      return;
    }

    if ((action.dataset.canvasAction || "") === "accept-quick-prompt") {
      resolveQuickPromptPreview(panel, action, menu, true);
      return;
    }

    if ((action.dataset.canvasAction || "") === "reject-quick-prompt") {
      resolveQuickPromptPreview(panel, action, menu, false);
      return;
    }

    if ((action.dataset.canvasAction || "") === "cancel-delete") {
      setDeleteConfirmVisibility(menu, false);
      return;
    }

    if ((action.dataset.canvasAction || "") === "confirm-delete") {
      deletePanel(panel);
      if (menu instanceof HTMLDetailsElement) {
        menu.open = false;
      }
      syncCanvasMenuLayering();
      return;
    }

    if ((action.dataset.canvasAction || "") === "generate") {
      generateCanvasImage(panel, action, menu);
      return;
    }

    if ((action.dataset.canvasAction || "") === "add-speech-bubble") {
      const bubble = addSpeechBubble(panel);
      if (bubble) {
        const text = bubble.querySelector("[data-bubble-text]");
        if (text instanceof HTMLElement) text.focus();
      }
      if (menu instanceof HTMLDetailsElement) {
        menu.open = false;
      }
      syncCanvasMenuLayering();
      return;
    }

    splitPanel(panel, action.dataset.splitDirection || "");

    if (menu instanceof HTMLDetailsElement) {
      menu.open = false;
    }
    syncCanvasMenuLayering();
  });

  document.addEventListener("pointerdown", (event) => {
    const bubbleAction = event.target.closest("[data-bubble-action]");
    if (bubbleAction instanceof HTMLElement && rootPanel.contains(bubbleAction)) {
      const bubble = bubbleAction.closest(".comic-speech-bubble");
      const actionName = bubbleAction.dataset.bubbleAction || "";
      if (
        bubble instanceof HTMLElement &&
        (actionName === "move" || actionName === "resize" || actionName === "pointer")
      ) {
        event.preventDefault();
        startBubblePointerEdit(bubble, event, actionName);
        return;
      }
    }

    const junction = event.target.closest(".comic-canvas-junction");
    if (junction instanceof HTMLElement) {
      event.preventDefault();
      startJunctionResize(junction, event);
      return;
    }

    const divider = event.target.closest("[data-canvas-divider]");
    if (!(divider instanceof HTMLElement)) return;
    event.preventDefault();
    startResize(divider, event);
  });

  document.addEventListener("dragstart", (event) => {
    if (!rootPanel.contains(event.target)) return;

    const panel = getEventCanvasPanel(event);
    if (!isChildCanvasPanel(panel) || isInteractiveCanvasControl(event.target)) {
      event.preventDefault();
      return;
    }

    draggedCanvasPanel = panel;
    panel.classList.add("is-canvas-swapping");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", ensureCanvasKey(panel));
    }
  });

  document.addEventListener("dragover", (event) => {
    const targetPanel = getEventCanvasPanel(event);
    if (!isValidCanvasSwapTarget(targetPanel)) return;

    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "move";
    }
    clearCanvasSwapState();
    targetPanel.classList.add("is-canvas-swap-target");
  });

  document.addEventListener("dragleave", (event) => {
    const panel = getEventCanvasPanel(event);
    if (!(panel instanceof HTMLElement) || panel.contains(event.relatedTarget)) return;
    panel.classList.remove("is-canvas-swap-target");
  });

  document.addEventListener("drop", (event) => {
    const targetPanel = getEventCanvasPanel(event);
    if (!isValidCanvasSwapTarget(targetPanel)) return;

    event.preventDefault();
    swapCanvasPanels(draggedCanvasPanel, targetPanel);
    clearCanvasSwapState();
  });

  document.addEventListener("dragend", () => {
    if (draggedCanvasPanel instanceof HTMLElement) {
      draggedCanvasPanel.classList.remove("is-canvas-swapping");
    }
    draggedCanvasPanel = null;
    clearCanvasSwapState();
  });

  if (menuVisibilityToggle instanceof HTMLButtonElement) {
    menuVisibilityToggle.addEventListener("click", () => {
      pageEditor.classList.toggle("is-canvas-menu-hidden");
      updateMenuVisibilityButton();
    });
  }

  if (canvasResetCancelButton instanceof HTMLButtonElement && canvasResetMenu instanceof HTMLDetailsElement) {
    canvasResetCancelButton.addEventListener("click", () => {
      canvasResetMenu.open = false;
    });
  }

  if (canvasResetConfirmButton instanceof HTMLButtonElement) {
    canvasResetConfirmButton.addEventListener("click", () => {
      resetCanvas();
      if (canvasResetMenu instanceof HTMLDetailsElement) {
        canvasResetMenu.open = false;
      }
      if (pageForm instanceof HTMLFormElement) {
        const stayInput = document.createElement("input");
        stayInput.type = "hidden";
        stayInput.name = "stay_on_page";
        stayInput.value = "1";
        pageForm.appendChild(stayInput);
        pageForm.requestSubmit();
      }
    });
  }

  window.addEventListener("resize", updateAllSpeechBubblePointers);

  ensureCanvasKey(rootPanel, "root");
  loadSavedLayout();
  loadCanvasImages();
  updateMenuVisibilityButton();
  rootPanel.querySelectorAll(".comic-canvas-menu").forEach((menu) => ensureMenuToggleListener(menu));
  syncCanvasMenuLayering();
  syncCanvasPanelDraggability();
})();
