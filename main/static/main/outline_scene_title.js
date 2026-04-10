(() => {
  const configEl = document.querySelector("[data-scene-rename-url]");
  if (!configEl) return;

  const renameUrl = configEl.getAttribute("data-scene-rename-url");
  if (!renameUrl) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const postRename = async (sceneId, title) => {
    const params = new URLSearchParams();
    params.set("scene_id", sceneId);
    params.set("title", title);

    const result = await ui.postFormUrlEncoded({
      url: renameUrl,
      params,
      csrfToken,
      failureLabel: "Rename failed",
    });
    if (!result.ok) {
      throw new Error(result.error);
    }
    return result.data?.title ?? "";
  };

  const wireInput = (input) => {
    const sceneItem = input.closest(".scene-item");
    const sceneId = sceneItem?.dataset?.sceneId;
    if (!sceneId) return;

    let lastSaved = (input.value || "").trim();

    const saveIfChanged = async () => {
      const next = (input.value || "").trim();
      if (next === lastSaved) return;

      input.disabled = true;
      try {
        const saved = await postRename(sceneId, next);
        input.value = saved;
        lastSaved = (saved || "").trim();
        ui.showMessage("Saved scene title.", "success", 2200);
      } catch (error) {
        input.value = lastSaved;
        ui.showMessage(error?.message || "Rename failed.", "error", 2200);
      } finally {
        input.disabled = false;
      }
    };

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        input.blur();
      } else if (event.key === "Escape") {
        event.preventDefault();
        input.value = lastSaved;
        input.blur();
      }
    });

    input.addEventListener("blur", () => {
      saveIfChanged();
    });
  };

  document.querySelectorAll(".scene-title-input").forEach(wireInput);
})();
