(() => {
  const configEl = document.querySelector("[data-scene-rename-url]");
  if (!configEl) return;

  const renameUrl = configEl.getAttribute("data-scene-rename-url");
  if (!renameUrl) return;

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  const csrfToken = getCookie("csrftoken");

  const showMessage = (text, level = "info") => {
    const list =
      document.querySelector(".messages") ||
      (() => {
        const ul = document.createElement("ul");
        ul.className = "messages";
        const main = document.querySelector("main.wrap") || document.body;
        main.insertBefore(ul, main.firstChild);
        return ul;
      })();

    const li = document.createElement("li");
    li.className = `message message-${level}`;
    li.textContent = text;
    list.appendChild(li);

    window.setTimeout(() => {
      li.remove();
    }, 2200);
  };

  const postRename = async (sceneId, title) => {
    const params = new URLSearchParams();
    params.set("scene_id", sceneId);
    params.set("title", title);

    const res = await fetch(renameUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
      },
      credentials: "same-origin",
      body: params.toString(),
    });

    const data = await res.json().catch(() => null);
    if (!res.ok || !data || data.ok !== true) {
      const errorText = data?.error || `Rename failed (${res.status})`;
      throw new Error(errorText);
    }
    return data.title ?? "";
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
        showMessage("Saved scene title.", "success");
      } catch (e) {
        input.value = lastSaved;
        showMessage(e?.message || "Rename failed.", "error");
      } finally {
        input.disabled = false;
      }
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      } else if (e.key === "Escape") {
        e.preventDefault();
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

