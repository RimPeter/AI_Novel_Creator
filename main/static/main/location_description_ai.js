(() => {
  const section = document.querySelector("[data-location-brainstorm-url]");
  const brainstormBtn = document.getElementById("location-brainstorm-btn");
  const addDetailsBtn = document.getElementById("location-add-details-btn");
  if (!section || !brainstormBtn || !addDetailsBtn) return;

  const brainstormUrl = section.getAttribute("data-location-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-location-add-details-url");
  const form = section.querySelector("form");
  if (!brainstormUrl || !addDetailsUrl || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

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
    const params = new URLSearchParams();
    params.set("name", getName());
    params.set("description", getDescription());
    for (const [k, v] of getObjectPairs()) {
      params.append("object_key", k);
      params.append("object_value", v);
    }

    const result = await ui.postFormUrlEncoded({
      url,
      params,
      csrfToken,
      failureLabel: "Request failed",
    });
    if (window.AIBillingGuard?.handleBillingResponse({ status: result.status }, result.data)) {
      return null;
    }
    if (!result.ok) {
      ui.showMessage(result.error, "error");
      return null;
    }

    const next = String(result.data?.suggestions?.description || "").trim();
    return next || "";
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
    if (window.AIBillingGuard?.redirectToBillingIfNeeded(section)) return;

    if (!getName()) {
      ui.showMessage("Add a location name first.", "warning");
      return;
    }
    if (getDescription()) {
      ui.showMessage("Description already has content; Brainstorm only fills empty fields.", "info");
      return;
    }

    brainstormBtn.disabled = true;
    const originalText = brainstormBtn.textContent;
    brainstormBtn.textContent = "Brainstorming...";

    try {
      const addition = await postForDescription(brainstormUrl);
      if (addition === null) return;
      if (!addition) {
        ui.showMessage("No suggestion returned.", "warning");
        return;
      }
      if (!setOrAppendDescription(addition)) ui.showMessage("Nothing to update.", "warning");
      else ui.showMessage("Filled description.", "success");
    } finally {
      brainstormBtn.disabled = false;
      brainstormBtn.textContent = originalText;
    }
  });

  addDetailsBtn.addEventListener("click", async () => {
    if (window.AIBillingGuard?.redirectToBillingIfNeeded(section)) return;

    if (!getName()) {
      ui.showMessage("Add a location name first.", "warning");
      return;
    }

    addDetailsBtn.disabled = true;
    const originalText = addDetailsBtn.textContent;
    addDetailsBtn.textContent = "Adding...";

    try {
      const addition = await postForDescription(addDetailsUrl);
      if (addition === null) return;
      if (!addition) {
        ui.showMessage("No additional details returned.", "warning");
        return;
      }
      if (!setOrAppendDescription(addition)) ui.showMessage("No new details to add right now.", "warning");
      else ui.showMessage("Enhanced description.", "success");
    } finally {
      addDetailsBtn.disabled = false;
      addDetailsBtn.textContent = originalText;
    }
  });
})();
