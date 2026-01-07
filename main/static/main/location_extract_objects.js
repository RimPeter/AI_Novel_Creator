(() => {
  const section = document.querySelector("[data-location-brainstorm-url]");
  const btn = document.getElementById("location-extract-objects-btn");
  if (!section || !btn) return;

  const extractUrl = section.getAttribute("data-location-extract-objects-url");
  const form = section.querySelector("form");
  if (!extractUrl || !form) return;

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

  const getValue = (name) => (form.querySelector(`[name="${name}"]`)?.value || "").trim();

  const getExistingObjectMap = () => {
    const keys = Array.from(form.querySelectorAll('input[name="object_key"]'));
    const values = Array.from(form.querySelectorAll('textarea[name="object_value"]'));
    const map = new Map();

    for (let i = 0; i < Math.max(keys.length, values.length); i += 1) {
      const k = (keys[i]?.value || "").trim();
      const v = (values[i]?.value || "").trim();
      if (!k && !v) continue;
      if (!k) continue;
      map.set(k.toLowerCase(), { key: k, value: v });
    }

    return map;
  };

  const addObjectRow = (key, value) => {
    const container = document.getElementById("object-rows");
    if (!container) return false;

    const row = document.createElement("div");
    row.className = "object-row";
    row.innerHTML = `
      <input class="form-control" name="object_key" type="text" placeholder="Object (key)" />
      <textarea class="form-control" name="object_value" rows="2" placeholder="Attributes (value)"></textarea>
      <button class="btn btn-secondary btn-sm remove-object-row" type="button">Remove</button>
    `;

    const keyEl = row.querySelector('input[name="object_key"]');
    const valEl = row.querySelector('textarea[name="object_value"]');
    if (keyEl) keyEl.value = key;
    if (valEl) valEl.value = value;

    container.appendChild(row);
    return true;
  };

  const fillExistingValueIfEmpty = (keyLower, value) => {
    const keys = Array.from(form.querySelectorAll('input[name="object_key"]'));
    for (const keyEl of keys) {
      const current = (keyEl.value || "").trim();
      if (!current || current.toLowerCase() !== keyLower) continue;
      const row = keyEl.closest(".object-row");
      const valEl = row?.querySelector?.('textarea[name="object_value"]');
      if (!valEl) return false;
      if ((valEl.value || "").trim()) return false;
      valEl.value = value;
      return true;
    }
    return false;
  };

  const postExtract = async () => {
    try {
      const params = new URLSearchParams();
      params.set("name", getValue("name"));
      params.set("description", getValue("description"));

      const existing = getExistingObjectMap();
      for (const { key, value } of existing.values()) {
        params.append("object_key", key);
        params.append("object_value", value);
      }

      const res = await fetch(extractUrl, {
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
        showMessage(data?.error || `Extract failed (${res.status})`, "error");
        return null;
      }
      return data.objects || {};
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
      return null;
    }
  };

  btn.addEventListener("click", async () => {
    const description = getValue("description");
    if (!description) {
      showMessage("Add a description first, then click Extract details.", "warning");
      return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Extracting...";

    try {
      const objects = await postExtract();
      if (objects === null) return;

      let added = 0;
      let filled = 0;
      const existing = getExistingObjectMap();

      for (const [rawKey, rawVal] of Object.entries(objects)) {
        const key = String(rawKey || "").trim();
        if (!key) continue;
        const keyLower = key.toLowerCase();
        const value = String(rawVal || "").trim();

        if (existing.has(keyLower)) {
          if (value) {
            const changed = fillExistingValueIfEmpty(keyLower, value);
            if (changed) filled += 1;
          }
          continue;
        }

        const ok = addObjectRow(key, value);
        if (ok) {
          existing.set(keyLower, { key, value });
          added += 1;
        }
      }

      if (!added && !filled) showMessage("No new objects found.", "warning");
      else if (filled && !added) showMessage(`Filled ${filled} object attribute(s).`, "success");
      else if (added && !filled) showMessage(`Added ${added} object(s).`, "success");
      else showMessage(`Added ${added} object(s) and filled ${filled} attribute(s).`, "success");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();

