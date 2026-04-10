(() => {
  const configEl = document.querySelector("[data-scene-move-url]");
  if (!configEl) return;

  const moveUrl = configEl.getAttribute("data-scene-move-url");
  if (!moveUrl) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  let draggedSceneId = null;
  let draggingEl = null;

  const clearDropHighlights = () => {
    document.querySelectorAll(".drop-over").forEach((el) => el.classList.remove("drop-over"));
  };

  const ensureDropzone = (listEl) => {
    if (!listEl) return;
    const hasScene = listEl.querySelector(".scene-item");
    const zone = listEl.querySelector(".scene-dropzone");
    if (hasScene && zone) {
      zone.remove();
      return;
    }
    if (!hasScene && !zone) {
      const li = document.createElement("li");
      li.className = "scene-dropzone";
      li.dataset.chapterId = listEl.dataset.chapterId;
      li.textContent = "No scenes yet - drop a scene here";
      listEl.appendChild(li);
    }
  };

  const applyDomMove = ({ sceneId, targetChapterId, beforeSceneId }) => {
    const sceneEl = document.querySelector(`.scene-item[data-scene-id="${sceneId}"]`);
    if (!sceneEl) return;

    const sourceList = sceneEl.closest(".scene-list");
    const targetList = document.querySelector(`.scene-list[data-chapter-id="${targetChapterId}"]`);
    if (!targetList) return;

    sceneEl.dataset.chapterId = targetChapterId;
    const beforeEl =
      beforeSceneId && beforeSceneId !== sceneId
        ? targetList.querySelector(`.scene-item[data-scene-id="${beforeSceneId}"]`)
        : null;

    if (beforeEl) targetList.insertBefore(sceneEl, beforeEl);
    else targetList.appendChild(sceneEl);

    ensureDropzone(sourceList);
    ensureDropzone(targetList);
  };

  const submitMove = async ({ sceneId, targetChapterId, beforeSceneId }) => {
    const params = new URLSearchParams();
    params.set("scene_id", sceneId);
    params.set("target_chapter_id", targetChapterId);
    if (beforeSceneId) params.set("before_scene_id", beforeSceneId);

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

    applyDomMove({ sceneId, targetChapterId, beforeSceneId });
    ui.showMessage("Moved scene.", "success", 2500);
  };

  document.addEventListener("dragstart", (event) => {
    const dragArea = event.target?.closest?.(".scene-drag");
    if (!dragArea) return;

    const sceneItem = dragArea.closest(".scene-item");
    if (!sceneItem) return;

    draggedSceneId = sceneItem.dataset.sceneId || null;
    draggingEl = sceneItem;
    if (!draggedSceneId) return;

    sceneItem.classList.add("is-dragging");
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedSceneId);
    } catch {
      // no-op
    }
  });

  document.addEventListener("dragend", () => {
    if (draggingEl) draggingEl.classList.remove("is-dragging");
    draggingEl = null;
    draggedSceneId = null;
    clearDropHighlights();
  });

  document.addEventListener("dragover", (event) => {
    if (!draggedSceneId) return;

    const sceneTarget = event.target?.closest?.(".scene-item");
    const listTarget = event.target?.closest?.(".scene-list");
    const dropZone = event.target?.closest?.(".scene-dropzone");
    if (!sceneTarget && !listTarget && !dropZone) return;

    event.preventDefault();
    clearDropHighlights();
    (sceneTarget || dropZone || listTarget).classList.add("drop-over");
  });

  document.addEventListener("drop", (event) => {
    if (!draggedSceneId) return;

    const sceneTarget = event.target?.closest?.(".scene-item");
    const listTarget = event.target?.closest?.(".scene-list");
    const dropZone = event.target?.closest?.(".scene-dropzone");
    if (!sceneTarget && !listTarget && !dropZone) return;

    event.preventDefault();
    const targetChapterId = (sceneTarget || dropZone || listTarget).dataset.chapterId;
    if (!targetChapterId) return;

    const beforeSceneId = sceneTarget?.dataset?.sceneId || null;
    const before = beforeSceneId && beforeSceneId !== draggedSceneId ? beforeSceneId : null;
    submitMove({ sceneId: draggedSceneId, targetChapterId, beforeSceneId: before });

    clearDropHighlights();
    if (draggingEl) draggingEl.classList.remove("is-dragging");
    draggingEl = null;
    draggedSceneId = null;
  });
})();
