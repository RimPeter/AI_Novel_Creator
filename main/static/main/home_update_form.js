(() => {
  const section = document.querySelector("[data-home-update-regenerate-url]");
  const button = document.getElementById("home-update-regenerate-btn");
  if (!section || !button) return;

  const form = section.querySelector("form");
  const url = section.getAttribute("data-home-update-regenerate-url");
  const bodyField = form?.querySelector('[name="body"]');
  const titlePreview = document.getElementById("home-update-title-preview");
  if (!form || !url || !bodyField || !titlePreview) return;

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  const csrfToken = getCookie("csrftoken");

  const deriveTitle = (rawText) => {
    const text = (rawText || "").trim();
    if (!text) return "Title will be generated from the body text.";

    const normalizedText = text.replace(/\s+/g, " ").trim().toLowerCase();
    if (
      normalizedText.includes("text generation model") ||
      normalizedText.includes("ai model") ||
      (normalizedText.includes("select") && normalizedText.includes("model") && normalizedText.includes("token usage"))
    ) {
      return "Added AI model selector";
    }
    if (normalizedText.includes("git commit") && (normalizedText.includes("helper") || normalizedText.includes("command"))) {
      return "Added Git commit command helper";
    }

    const lines = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    let firstLine = lines[0] || text;
    firstLine = firstLine.replace(/^\s*we have introduced a new feature that allows (?:each )?user(?:s)? to\s+/i, "Added ");
    firstLine = firstLine.replace(/^\s*this update (?:adds|introduces)\s+/i, "Added ");
    firstLine = firstLine.replace(/^\s*[-*#]+\s*/, "");
    firstLine = firstLine.replace(/^\s*(feat|fix|chore|refactor|docs|style|test|tests|perf)\s*:\s*/i, "");
    firstLine = firstLine.replace(/\s+/g, " ").trim().replace(/^[.:\-\s]+|[.:\-\s]+$/g, "");
    if (!firstLine) return "Title will be generated from the body text.";

    const sentenceMatch = firstLine.match(/(.+?[.!?])(?:\s|$)/);
    let title = sentenceMatch ? sentenceMatch[1].trim() : firstLine;
    title = title.replace(/[.!?]+$/g, "").trim();
    if (title.length > 72) {
      const shortened = title.slice(0, 69).replace(/\s+\S*$/, "").trim();
      title = `${shortened || title.slice(0, 69).trim()}...`;
    }
    if (!title) return "Title will be generated from the body text.";
    return title.charAt(0).toUpperCase() + title.slice(1);
  };

  const updateTitlePreview = (value) => {
    titlePreview.textContent = deriveTitle(value);
  };

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
      showMessage("Add commit text to Body first.", "warning");
      return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Regenerating...";

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
        showMessage(data?.error || `Regenerate failed (${res.status})`, "error");
        return;
      }

      bodyField.value = data.body || bodyField.value;
      bodyField.dispatchEvent(new Event("input", { bubbles: true }));
      titlePreview.textContent = data.title || deriveTitle(bodyField.value);
      showMessage(data.warning ? `Update text kept. Title will be generated on post. ${data.warning}` : "Update text regenerated.", data.warning ? "warning" : "success");
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });

  bodyField.addEventListener("input", () => {
    updateTitlePreview(bodyField.value);
  });

  updateTitlePreview(bodyField.value);
})();
