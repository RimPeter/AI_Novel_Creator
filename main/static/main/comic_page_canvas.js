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
  if (!rootPanel || !(layoutInput instanceof HTMLInputElement) || !(pageEditor instanceof HTMLElement)) return;

  const MIN_PANEL_SIZE = 80;
  let splitIdSequence = 0;
  let canvasKeySequence = 0;
  const usedCanvasKeys = new Set();

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

  const syncCanvasMenuLayering = () => {
    rootPanel.querySelectorAll(".is-canvas-menu-active").forEach((node) => {
      node.classList.remove("is-canvas-menu-active");
    });

    rootPanel.querySelectorAll(".comic-canvas-menu").forEach((menu) => {
      if (!(menu instanceof HTMLDetailsElement) || !menu.open) {
        setDeleteConfirmVisibility(menu, false);
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
        return { type: "panel", canvas_key: ensureCanvasKey(panel) };
      }

      const childPanels = getChildPanels(split);
      return {
        type: "split",
        canvas_key: ensureCanvasKey(panel),
        direction: split.dataset.splitDirection || "vertical",
        ratio: parseRatio(childPanels[0] || panel),
        children: childPanels.map((childPanel) => serializePanel(childPanel)),
      };
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

    splitPanel(panel, action.dataset.splitDirection || "");

    if (menu instanceof HTMLDetailsElement) {
      menu.open = false;
    }
    syncCanvasMenuLayering();
  });

  document.addEventListener("pointerdown", (event) => {
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

  ensureCanvasKey(rootPanel, "root");
  loadSavedLayout();
  loadCanvasImages();
  updateMenuVisibilityButton();
  rootPanel.querySelectorAll(".comic-canvas-menu").forEach((menu) => ensureMenuToggleListener(menu));
  syncCanvasMenuLayering();
})();
