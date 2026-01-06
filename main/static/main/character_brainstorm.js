(() => {
  const section = document.querySelector("[data-character-brainstorm-url]");
  const btn = document.getElementById("character-brainstorm-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-character-brainstorm-url");
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
      const params = new URLSearchParams();
      for (const name of FIELD_NAMES) {
        params.set(name, current[name] || "");
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
        showMessage(data?.error || `Brainstorm failed (${res.status})`, "error");
        return;
      }

      const filled = fillEmptyFields(data.suggestions);
      if (!filled) {
        showMessage("No suggestions returned for empty fields.", "warning");
      } else {
        showMessage(`Filled ${filled} field(s).`, "success");
      }
    } catch (e) {
      showMessage(`Brainstorm failed: ${e?.message || e}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();

