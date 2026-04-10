(() => {
  const section = document.querySelector("[data-character-portrait-url]");
  const btn = document.getElementById("character-portrait-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-character-portrait-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const img = document.getElementById("character-portrait-img");
  const placeholder = document.getElementById("character-portrait-placeholder");
  const status = document.getElementById("character-portrait-status");
  const getFieldValue = (name) => (form.querySelector(`[name="${name}"]`)?.value || "").trim();

  btn.addEventListener("click", async () => {
    if (!getFieldValue("name")) {
      ui.showMessage("Add a name first, then create a portrait.", "warning");
      return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Creating...";

    try {
      const result = await ui.postFormUrlEncoded({
        url,
        params: new URLSearchParams(new FormData(form)),
        csrfToken,
        failureLabel: "Portrait failed",
      });
      if (!result.ok) {
        ui.showMessage(result.error, "error");
        if (status) status.textContent = result.error;
        return;
      }

      if (img && result.data?.portrait_url) {
        img.src = result.data.portrait_url;
        img.classList.remove("is-hidden");
      }
      if (placeholder) placeholder.classList.add("is-hidden");
      if (status) status.textContent = "Portrait saved for this character.";
      ui.showMessage("Portrait created.", "success");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();
