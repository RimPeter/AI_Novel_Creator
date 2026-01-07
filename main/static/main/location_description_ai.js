(() => {
  const section = document.querySelector("[data-location-brainstorm-url]");
  const brainstormBtn = document.getElementById("location-brainstorm-btn");
  const addDetailsBtn = document.getElementById("location-add-details-btn");
  if (!section || !brainstormBtn || !addDetailsBtn) return;

  const brainstormUrl = section.getAttribute("data-location-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-location-add-details-url");
  const form = section.querySelector("form");
  if (!brainstormUrl || !addDetailsUrl || !form) return;

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
    }, 3000);
  };

  const getField = (name) => form.querySelector(`[name="${name}"]`);
  const getName = () => (getField("name")?.value || "").trim();
  const getDescription = () => (getField("description")?.value || "").trim();

  const getObjectPairs = () => {
    const keys = Array.from(form.querySelectorAll('input[name="object_key"]'));
    const values = Array.from(form.querySelectorAll('textarea[name="object_value"]'));
    const pairs = [];

    for (let i = 0; i < Math.max(keys.length, values.length); i += 1) {
      const key = (keys[i]?.value || "").trim();
      const value = (values[i]?.value || "").trim();
      pairs.push([key, value]);
    }

    return pairs;
  };

  const postForDescription = async (url) => {
    try {
      const params = new URLSearchParams();
      params.set("name", getName());
      params.set("description", getDescription());
      for (const [k, v] of getObjectPairs()) {
        params.append("object_key", k);
        params.append("object_value", v);
      }

      const res = await fetch(url, {
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
        showMessage(data?.error || `Request failed (${res.status})`, "error");
        return null;
      }

      const next = String(data?.suggestions?.description || "").trim();
      return next || "";
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
      return null;
    }
  };

  const setOrAppendDescription = (addition) => {
    const el = getField("description");
    if (!el) return false;

    const existing = (el.value || "").trim();
    if (!existing) {
      el.value = addition.trim();
      return true;
    }

    if (!addition.trim() || existing.includes(addition.trim())) return false;
    el.value = `${existing}\n\n${addition.trim()}`.trim();
    return true;
  };

  brainstormBtn.addEventListener("click", async () => {
    if (!getName()) {
      showMessage("Add a location name first.", "warning");
      return;
    }
    if (getDescription()) {
      showMessage("Description already has content; Brainstorm only fills empty fields.", "info");
      return;
    }

    brainstormBtn.disabled = true;
    const originalText = brainstormBtn.textContent;
    brainstormBtn.textContent = "Brainstorming...";

    try {
      const addition = await postForDescription(brainstormUrl);
      if (addition === null) return;
      if (!addition) {
        showMessage("No suggestion returned.", "warning");
        return;
      }
      const changed = setOrAppendDescription(addition);
      if (!changed) showMessage("Nothing to update.", "warning");
      else showMessage("Filled description.", "success");
    } finally {
      brainstormBtn.disabled = false;
      brainstormBtn.textContent = originalText;
    }
  });

  addDetailsBtn.addEventListener("click", async () => {
    if (!getName()) {
      showMessage("Add a location name first.", "warning");
      return;
    }

    addDetailsBtn.disabled = true;
    const originalText = addDetailsBtn.textContent;
    addDetailsBtn.textContent = "Adding...";

    try {
      const addition = await postForDescription(addDetailsUrl);
      if (addition === null) return;
      if (!addition) {
        showMessage("No additional details returned.", "warning");
        return;
      }
      const changed = setOrAppendDescription(addition);
      if (!changed) showMessage("No new details to add right now.", "warning");
      else showMessage("Enhanced description.", "success");
    } finally {
      addDetailsBtn.disabled = false;
      addDetailsBtn.textContent = originalText;
    }
  });
})();

