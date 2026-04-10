(() => {
  const configEl = document.querySelector("[data-location-move-url]");
  if (!configEl) return;

  const moveUrl = configEl.getAttribute("data-location-move-url");
  if (!moveUrl) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  let draggedLocationId = null;

  const getLocationTarget = (event) => event.target?.closest?.(".world-location-box, .location-tree-node");

  const clearDropHighlights = () => {
    document.querySelectorAll(".location-drop-over").forEach((el) => el.classList.remove("location-drop-over"));
    document.querySelectorAll(".is-location-dragging").forEach((el) => el.classList.remove("is-location-dragging"));
  };

  const submitMove = async ({ locationId, targetParentId }) => {
    const params = new URLSearchParams();
    params.set("location_id", locationId);
    params.set("target_parent_id", targetParentId);

    const result = await ui.postFormUrlEncoded({
      url: moveUrl,
      params,
      csrfToken,
      failureLabel: "Move failed",
    });
    if (!result.ok) {
      ui.showMessage(result.error, "error", 2500);
      return;
    }

    ui.showMessage("Location moved.", "success", 2500);
    window.location.reload();
  };

  document.addEventListener("dragstart", (event) => {
    const box = getLocationTarget(event);
    if (!box || box.getAttribute("draggable") !== "true") return;

    draggedLocationId = box.dataset.locationId || null;
    if (!draggedLocationId) return;

    box.classList.add("is-location-dragging");
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedLocationId);
    } catch {
      // no-op
    }
  });

  document.addEventListener("dragend", () => {
    draggedLocationId = null;
    clearDropHighlights();
  });

  document.addEventListener("dragover", (event) => {
    if (!draggedLocationId) return;
    const box = getLocationTarget(event);
    if (!box) return;
    if (box.dataset.locationId === draggedLocationId) return;

    event.preventDefault();
    clearDropHighlights();
    box.classList.add("location-drop-over");
  });

  document.addEventListener("drop", (event) => {
    if (!draggedLocationId) return;

    const box = getLocationTarget(event);
    if (!box) return;

    event.preventDefault();
    const targetParentId = box.dataset.locationId;
    if (!targetParentId || targetParentId === draggedLocationId) return;

    submitMove({ locationId: draggedLocationId, targetParentId });
    clearDropHighlights();
    draggedLocationId = null;
  });
})();
