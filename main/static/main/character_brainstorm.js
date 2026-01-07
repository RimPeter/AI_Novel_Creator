(() => {
  const section = document.querySelector("[data-character-brainstorm-url]");
  const btn = document.getElementById("character-brainstorm-btn");
  const addDetailsBtn = document.getElementById("character-add-details-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-character-brainstorm-url");
  const addDetailsUrl = section.getAttribute("data-character-add-details-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

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
      if (value === null || value === undefined) continue;
      const next = String(value).trim();
      if (!next) continue;
      el.value = next;
      filled += 1;
    }
    return filled;
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
        showMessage(data?.error || `Brainstorm failed (${res.status})`, "error");
        return null;
      }

      return data.suggestions || {};
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
      return null;
    }
  };

  btn.addEventListener("click", async () => {
    const current = getCurrentValues();
    const empties = FIELD_NAMES.filter((name) => !current[name]);
    if (!empties.length) {
      showMessage("Nothing to fill — all fields already have values.", "info");
      return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Brainstorming...";

    try {
      const suggestions = await postForSuggestions(url);
      if (!suggestions) return;
      const filled = fillEmptyFields(suggestions);
      if (!filled) showMessage("No suggestions returned for empty fields.", "warning");
      else showMessage(`Filled ${filled} field(s).`, "success");
    } finally {
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

      if (existing.includes(addition)) continue;
      el.value = `${existing}\n\n${addition}`.trim();
      changed += 1;
    }
    return changed;
  };

  if (addDetailsBtn && addDetailsUrl) {
    addDetailsBtn.addEventListener("click", async () => {
      const current = getCurrentValues();
      if (!current.name) {
        showMessage("Add a name first, then click Add more details.", "warning");
        return;
      }

      addDetailsBtn.disabled = true;
      const originalText = addDetailsBtn.textContent;
      addDetailsBtn.textContent = "Adding...";

      try {
        const suggestions = await postForSuggestions(addDetailsUrl);
        if (!suggestions) return;
        const changed = applyMoreDetails(suggestions);
        if (!changed) showMessage("No additional details to add right now.", "warning");
        else showMessage(`Enhanced ${changed} field(s).`, "success");
      } finally {
        addDetailsBtn.disabled = false;
        addDetailsBtn.textContent = originalText;
      }
    });
  }
})();
