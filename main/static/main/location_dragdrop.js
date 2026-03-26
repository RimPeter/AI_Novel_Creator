(() => {
  const configEl = document.querySelector("[data-location-move-url]");
  if (!configEl) return;

  const moveUrl = configEl.getAttribute("data-location-move-url");
  if (!moveUrl) return;

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  const csrfToken = getCookie("csrftoken");
  let draggedLocationId = null;

  const getLocationTarget = (event) => event.target?.closest?.(".world-location-box, .location-tree-node");

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

    window.setTimeout(() => li.remove(), 2500);
  };

  const clearDropHighlights = () => {
    document.querySelectorAll(".location-drop-over").forEach((el) => el.classList.remove("location-drop-over"));
    document.querySelectorAll(".is-location-dragging").forEach((el) => el.classList.remove("is-location-dragging"));
  };

  const submitMove = async ({ locationId, targetParentId }) => {
    try {
      const params = new URLSearchParams();
      params.set("location_id", locationId);
      params.set("target_parent_id", targetParentId);

      const res = await fetch(moveUrl, {
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
        showMessage(data?.error || `Move failed (${res.status})`, "error");
        return;
      }

      showMessage("Location moved.", "success");
      window.location.reload();
    } catch (e) {
      showMessage(`Move failed: ${e?.message || e}`, "error");
    }
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
