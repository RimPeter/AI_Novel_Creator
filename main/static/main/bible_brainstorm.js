(() => {
  const section = document.querySelector("[data-bible-brainstorm-url]");
  const button = document.getElementById("bible-brainstorm-btn");
  if (!section || !button) return;

  const url = section.getAttribute("data-bible-brainstorm-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

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

  const postForSuggestions = async () => {
    const params = new URLSearchParams();
    const current = getCurrentValues();
    for (const name of FIELD_NAMES) {
      params.set(name, current[name] || "");
    }

    const result = await ui.postFormUrlEncoded({
      url,
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

  button.addEventListener("click", async () => {
    const current = getCurrentValues();
    const empties = FIELD_NAMES.filter((name) => !current[name]);
    if (!empties.length) {
      ui.showMessage("Nothing to fill - all fields already have values.", "info");
      return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Brainstorming...";

    try {
      const suggestions = await postForSuggestions();
      if (!suggestions) return;
      const filled = fillEmptyFields(suggestions);
      if (!filled) ui.showMessage("No suggestions returned for empty fields.", "warning");
      else ui.showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
})();
