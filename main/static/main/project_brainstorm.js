(() => {
  const section = document.querySelector("[data-project-brainstorm-url]");
  const brainstormBtn = document.getElementById("project-brainstorm-btn");
  const addDetailsBtn = document.getElementById("project-add-details-btn");
  if (!section || !brainstormBtn) return;

  const brainstormUrl = section.getAttribute("data-project-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-project-add-details-url");
  const form = section.querySelector("form");
  if (!brainstormUrl || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const FIELD_NAMES = ["seed_idea", "genre", "tone", "style_notes"];

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

  const postForSuggestions = async (postUrl) => {
    const current = getCurrentValues();
    const params = new URLSearchParams();
    params.set("title", (getFieldEl("title")?.value || "").trim());
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

  const applyMoreDetails = (suggestions) => {
    const dedupeAppendedText = (existing, addition) => {
      const existingText = String(existing || "").trim();
      const additionText = String(addition || "").trim();
      if (!additionText) return "";
      if (!existingText) return additionText;

      const existingLower = existingText.toLowerCase();
      const additionLower = additionText.toLowerCase();
      if (existingLower.includes(additionLower)) return "";

      const trimOverlap = (text) => text.replace(/^[\s;,:.-]+/, "").trim();

      if (additionLower.startsWith(existingLower)) {
        return trimOverlap(additionText.slice(existingText.length));
      }

      const maxOverlap = Math.min(existingText.length, additionText.length);
      for (let overlap = maxOverlap; overlap > 0; overlap -= 1) {
        if (existingLower.slice(-overlap) === additionLower.slice(0, overlap)) {
          return trimOverlap(additionText.slice(overlap));
        }
      }

      return additionText;
    };

    let changed = 0;
    for (const [name, value] of Object.entries(suggestions || {})) {
      const el = getFieldEl(name);
      if (!el) continue;
      const addition = String(value || "").trim();
      if (!addition) continue;

      const existing = (el.value || "").trim();
      if (!existing) {
        el.value = addition;
        changed += 1;
        continue;
      }

      const extra = dedupeAppendedText(existing, addition);
      if (!extra) continue;
      el.value = `${existing}\n\n${extra}`.trim();
      changed += 1;
    }
    return changed;
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
      const filled = fillEmptyFields(suggestions);
      if (!filled) ui.showMessage("No suggestions returned for empty fields.", "warning");
      else ui.showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
      brainstormBtn.disabled = false;
      brainstormBtn.textContent = originalText;
    }
  });

  if (addDetailsBtn && addDetailsUrl) {
    addDetailsBtn.addEventListener("click", async () => {
      const current = getCurrentValues();
      if (!Object.values(current).some(Boolean)) {
        ui.showMessage("Add at least one project detail first.", "warning");
        return;
      }

      addDetailsBtn.disabled = true;
      const originalText = addDetailsBtn.textContent;
      addDetailsBtn.textContent = "Adding...";

      try {
        const suggestions = await postForSuggestions(addDetailsUrl);
        if (!suggestions) return;
        const changed = applyMoreDetails(suggestions);
        if (!changed) ui.showMessage("No additional details to add right now.", "warning");
        else ui.showMessage(`Enhanced ${changed} field(s).`, "success");
      } finally {
        addDetailsBtn.disabled = false;
        addDetailsBtn.textContent = originalText;
      }
    });
  }
})();
