(() => {
  const section = document.querySelector("[data-bible-brainstorm-url]");
  const brainstormBtn = document.getElementById("bible-brainstorm-btn");
  const addDetailsBtn = document.getElementById("bible-add-details-btn");
  if (!section || !brainstormBtn || !addDetailsBtn) return;

  const brainstormUrl = section.getAttribute("data-bible-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-bible-add-details-url");
  const form = section.querySelector("form");
  if (!brainstormUrl || !addDetailsUrl || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const FIELD_NAMES = ["summary_md", "constraints", "facts"];

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

  const fillEmptyFields = (suggestions) => {
    let filled = 0;
    for (const [name, value] of Object.entries(suggestions || {})) {
      const el = getFieldEl(name);
      if (!el) continue;
      if ((el.value || "").trim()) continue;
      const next = String(value || "").trim();
      if (!next) continue;
      el.value = next;
      filled += 1;
    }
    return filled;
  };

  const appendOrSet = (name, value) => {
    const el = getFieldEl(name);
    if (!el) return false;
    const addition = String(value || "").trim();
    if (!addition) return false;

    const existing = (el.value || "").trim();
    if (!existing) {
      el.value = addition;
      return true;
    }
    if (existing.includes(addition)) return false;
    el.value = `${existing}\n\n${addition}`.trim();
    return true;
  };

  const postForSuggestions = async (postUrl, failureLabel) => {
    const params = new URLSearchParams();
    const current = getCurrentValues();
    for (const name of FIELD_NAMES) {
      params.set(name, current[name] || "");
    }

    const result = await ui.postFormUrlEncoded({
      url: postUrl,
      params,
      csrfToken,
      failureLabel,
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
      const suggestions = await postForSuggestions(brainstormUrl, "Brainstorm failed");
      if (!suggestions) return;
      const filled = fillEmptyFields(suggestions);
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
      ui.showMessage("Add at least one story bible detail first.", "warning");
      return;
    }

    addDetailsBtn.disabled = true;
    const originalText = addDetailsBtn.textContent;
    addDetailsBtn.textContent = "Adding...";

    try {
      const suggestions = await postForSuggestions(addDetailsUrl, "Request failed");
      if (!suggestions) return;
      let changed = 0;
      for (const [name, value] of Object.entries(suggestions || {})) {
        if (appendOrSet(name, value)) changed += 1;
      }
      if (!changed) ui.showMessage("No additional details to add right now.", "warning");
      else ui.showMessage(`Enhanced ${changed} field(s).`, "success");
    } finally {
      addDetailsBtn.disabled = false;
      addDetailsBtn.textContent = originalText;
    }
  });
})();
