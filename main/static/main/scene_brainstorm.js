(() => {
  const section = document.querySelector("[data-scene-brainstorm-url]");
  const brainstormBtn = document.getElementById("scene-brainstorm-btn");
  const addDetailsBtn = document.getElementById("scene-add-details-btn");
  if (!section || !brainstormBtn || !addDetailsBtn) return;

  const brainstormUrl = section.getAttribute("data-scene-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-scene-add-details-url");
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

  const FIELD_NAMES = ["title", "summary", "pov", "location"];

  const getFieldEl = (name) => form.querySelector(`[name="${name}"]`);

  const getCurrentValues = () => {
    const values = {};
    for (const name of FIELD_NAMES) {
      const el = getFieldEl(name);
      if (!el) continue;
      values[name] = (el.value || "").trim();
    }
    return values;
  };

  const setIfEmpty = (name, value) => {
    const el = getFieldEl(name);
    if (!el) return false;
    if ((el.value || "").trim()) return false;
    const next = String(value || "").trim();
    if (!next) return false;

    if (el.tagName === "SELECT") {
      const hasOption = Array.from(el.options || []).some((opt) => opt.value === next);
      if (!hasOption) return false;
    }

    el.value = next;
    return true;
  };

  const appendOrSet = (name, value) => {
    const el = getFieldEl(name);
    if (!el) return false;
    const addition = String(value || "").trim();
    if (!addition) return false;

    if (el.tagName === "SELECT") {
      const existing = (el.value || "").trim();
      if (existing) return false;
      const hasOption = Array.from(el.options || []).some((opt) => opt.value === addition);
      if (!hasOption) return false;
      el.value = addition;
      return true;
    }

    const existing = (el.value || "").trim();
    if (!existing) {
      el.value = addition;
      return true;
    }
    if (existing.includes(addition)) return false;
    el.value = `${existing}\n\n${addition}`.trim();
    return true;
  };

  const postForSuggestions = async (postUrl) => {
    const current = getCurrentValues();

    try {
      const params = new URLSearchParams();
      for (const name of FIELD_NAMES) {
        params.set(name, current[name] || "");
      }

      const res = await fetch(postUrl, {
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

      return data.suggestions || {};
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
      return null;
    }
  };

  brainstormBtn.addEventListener("click", async () => {
    const current = getCurrentValues();
    const empties = FIELD_NAMES.filter((name) => !current[name]);
    if (!empties.length) {
      showMessage("Nothing to fill — all fields already have values.", "info");
      return;
    }

    brainstormBtn.disabled = true;
    const originalText = brainstormBtn.textContent;
    brainstormBtn.textContent = "Brainstorming...";

    try {
      const suggestions = await postForSuggestions(brainstormUrl);
      if (!suggestions) return;
      let filled = 0;
      for (const [name, value] of Object.entries(suggestions)) {
        if (setIfEmpty(name, value)) filled += 1;
      }
      if (!filled) showMessage("No suggestions returned for empty fields.", "warning");
      else showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
      brainstormBtn.disabled = false;
      brainstormBtn.textContent = originalText;
    }
  });

  addDetailsBtn.addEventListener("click", async () => {
    const current = getCurrentValues();
    if (!Object.values(current).some(Boolean)) {
      showMessage("Add at least one scene detail first.", "warning");
      return;
    }

    addDetailsBtn.disabled = true;
    const originalText = addDetailsBtn.textContent;
    addDetailsBtn.textContent = "Adding...";

    try {
      const suggestions = await postForSuggestions(addDetailsUrl);
      if (!suggestions) return;
      let changed = 0;
      for (const [name, value] of Object.entries(suggestions)) {
        if (name === "summary") {
          if (appendOrSet(name, value)) changed += 1;
        } else {
          if (setIfEmpty(name, value)) changed += 1;
        }
      }
      if (!changed) showMessage("No additional details to add right now.", "warning");
      else showMessage(`Enhanced ${changed} field(s).`, "success");
    } finally {
      addDetailsBtn.disabled = false;
      addDetailsBtn.textContent = originalText;
    }
  });
})();

