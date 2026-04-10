(() => {
  const section = document.querySelector("[data-home-update-regenerate-url]");
  const button = document.getElementById("home-update-regenerate-btn");
  if (!section || !button) return;

  const form = section.querySelector("form");
  const url = section.getAttribute("data-home-update-regenerate-url");
  const titleField = form?.querySelector('[name="title"]');
  const bodyField = form?.querySelector('[name="body"]');
  if (!form || !url || !titleField || !bodyField) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  button.addEventListener("click", async () => {
    if (!(bodyField.value || "").trim()) {
      ui.showMessage("Paste raw git text into Body text first.", "warning", 3500);
      return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Generating...";

    try {
      const params = new URLSearchParams();
      params.set("body", bodyField.value || "");

      const result = await ui.postFormUrlEncoded({
        url,
        params,
        csrfToken,
        failureLabel: "Generation failed",
      });
      if (!result.ok) {
        ui.showMessage(result.error, "error", 3500);
        return;
      }

      titleField.value = result.data?.title || titleField.value;
      bodyField.value = result.data?.body || bodyField.value;
      bodyField.dispatchEvent(new Event("input", { bubbles: true }));
      ui.showMessage(
        result.data?.warning ? `Used fallback generation. ${result.data.warning}` : "Title and body generated.",
        result.data?.warning ? "warning" : "success",
        3500
      );
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
})();
