(() => {
  const section = document.querySelector("[data-home-update-regenerate-url]");
  const button = document.getElementById("home-update-regenerate-btn");
  if (!section || !button) return;

  const form = section.querySelector("form");
  const url = section.getAttribute("data-home-update-regenerate-url");
  const titleField = form?.querySelector('[name="title"]');
  const bodyField = form?.querySelector('[name="body"]');
  if (!form || !url || !titleField || !bodyField) return;

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
    }, 3500);
  };

  button.addEventListener("click", async () => {
    if (!(bodyField.value || "").trim()) {
      showMessage("Paste raw git text into Body text first.", "warning");
      return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Generating...";

    try {
      const params = new URLSearchParams();
      params.set("body", bodyField.value || "");

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
        showMessage(data?.error || `Generation failed (${res.status})`, "error");
        return;
      }

      titleField.value = data.title || titleField.value;
      bodyField.value = data.body || bodyField.value;
      bodyField.dispatchEvent(new Event("input", { bubbles: true }));
      showMessage(data.warning ? `Used fallback generation. ${data.warning}` : "Title and body generated.", data.warning ? "warning" : "success");
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
})();
