(() => {
  const section = document.querySelector("[data-character-brainstorm-url]");
  const btn = document.getElementById("character-brainstorm-btn");
  const addDetailsBtn = document.getElementById("character-add-details-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-character-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-character-add-details-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const FIELD_NAMES = [
    "name",
    "role",
    "age",
    "gender",
    "personality",
    "appearance",
    "background",
    "goals",
    "voice_notes",
    "description",
  ];

  const getFieldEl = (name) => form.querySelector(`[name="${name}"]`);

  const buildParams = (rejected) => {
    const params = new URLSearchParams(new FormData(form));
    if (rejected) {
      for (const field of rejected) {
        const key = `reject_${field}`;
        if (!params.has(key)) params.append(key, "on");
      }
    }
    return params;
  };

  const getRejectedFields = () => {
    const rejected = new Set();
    for (const name of FIELD_NAMES) {
      const checkbox = form.querySelector(`[name="reject_${name}"]`);
      if (checkbox?.checked) rejected.add(name);
    }
    return rejected;
  };

  const clearRejectedCheckboxes = () => {
    for (const name of FIELD_NAMES) {
      const checkbox = form.querySelector(`[name="reject_${name}"]`);
      if (checkbox) checkbox.checked = false;
    }
  };

  const getCurrentValues = () => {
    const values = {};
    for (const name of FIELD_NAMES) {
      const el = getFieldEl(name);
      if (!el) continue;
      values[name] = (el.value || "").trim();
    }
    return values;
  };

  const fillEmptyFields = (suggestions, rejected) => {
    let filled = 0;
    for (const [name, value] of Object.entries(suggestions || {})) {
      const el = getFieldEl(name);
      if (!el) continue;
      if (!rejected?.has?.(name) && (el.value || "").trim()) continue;
      if (value === null || value === undefined) continue;
      const next = String(value).trim();
      if (!next) continue;
      el.value = next;
      filled += 1;
    }
    return filled;
  };

  const postForSuggestions = async (postUrl) => {
    const rejected = getRejectedFields();
    const params = buildParams(rejected);

    const result = await ui.postFormUrlEncoded({
      url: postUrl,
      params,
      csrfToken,
      failureLabel: "Brainstorm failed",
    });
    if (!result.ok) {
      ui.showMessage(result.error, "error");
      return null;
    }

    return result.data?.suggestions || {};
  };

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

  btn.addEventListener("click", async () => {
    const current = getCurrentValues();
    const rejected = getRejectedFields();
    const empties = FIELD_NAMES.filter((name) => !current[name] || rejected.has(name));
    if (!empties.length && rejected.size === 0) {
      ui.showMessage("Nothing to fill - all fields already have values.", "info");
      return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Brainstorming...";

    try {
      const suggestions = await postForSuggestions(url);
      if (!suggestions) return;
      const filled = fillEmptyFields(suggestions, rejected);
      if (!filled) ui.showMessage("No suggestions returned for empty fields.", "warning");
      else ui.showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
      clearRejectedCheckboxes();
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  const applyMoreDetails = (suggestions) => {
    let changed = 0;
    for (const [name, value] of Object.entries(suggestions || {})) {
      const el = getFieldEl(name);
      if (!el) continue;
      if (value === null || value === undefined) continue;
      const addition = String(value).trim();
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

  if (addDetailsBtn && addDetailsUrl) {
    addDetailsBtn.addEventListener("click", async () => {
      const current = getCurrentValues();
      if (!current.name) {
        ui.showMessage("Add a name first, then click Add more details.", "warning");
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
