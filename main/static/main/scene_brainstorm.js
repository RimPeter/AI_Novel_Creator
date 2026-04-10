(() => {
  const section = document.querySelector("[data-scene-brainstorm-url]");
  const brainstormBtn = document.getElementById("scene-brainstorm-btn");
  const addDetailsBtn = document.getElementById("scene-add-details-btn");
  if (!section || !brainstormBtn || !addDetailsBtn) return;

  const brainstormUrl = section.getAttribute("data-scene-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-scene-add-details-url");
  const form = section.querySelector("form");
  if (!brainstormUrl || !addDetailsUrl || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

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
    const params = new URLSearchParams();
    for (const name of FIELD_NAMES) {
      params.set(name, current[name] || "");
    }

    const result = await ui.postFormUrlEncoded({
      url: postUrl,
      params,
      csrfToken,
      failureLabel: "Request failed",
    });
    if (!result.ok) {
      ui.showMessage(result.error, "error");
      return null;
    }

    return result.data?.suggestions || {};
  };

  brainstormBtn.addEventListener("click", async () => {
    const current = getCurrentValues();
    const empties = FIELD_NAMES.filter((name) => !current[name]);
    if (!empties.length) {
      ui.showMessage("Nothing to fill - all fields already have values.", "info");
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
      if (!filled) ui.showMessage("No suggestions returned for empty fields.", "warning");
      else ui.showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
      brainstormBtn.disabled = false;
      brainstormBtn.textContent = originalText;
    }
  });

  addDetailsBtn.addEventListener("click", async () => {
    const current = getCurrentValues();
    if (!Object.values(current).some(Boolean)) {
      ui.showMessage("Add at least one scene detail first.", "warning");
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
        } else if (setIfEmpty(name, value)) {
          changed += 1;
        }
      }
      if (!changed) ui.showMessage("No additional details to add right now.", "warning");
      else ui.showMessage(`Enhanced ${changed} field(s).`, "success");
    } finally {
      addDetailsBtn.disabled = false;
      addDetailsBtn.textContent = originalText;
    }
  });
})();
