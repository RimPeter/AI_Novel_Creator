(() => {
  const section = document.querySelector("[data-location-image-url]");
  const btn = document.getElementById("location-image-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-location-image-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

  const ui = window.AppUI;
  if (!ui) return;
  const csrfToken = ui.getCsrfToken();

  const img = document.getElementById("location-image-img");
  const placeholder = document.getElementById("location-image-placeholder");
  const status = document.getElementById("location-image-status");
  const getFieldValue = (name) => (form.querySelector(`[name="${name}"]`)?.value || "").trim();

  btn.addEventListener("click", async () => {
    if (window.AIBillingGuard?.redirectToBillingIfNeeded(section)) return;

    if (!getFieldValue("name")) {
      ui.showMessage("Add a name first, then create an image.", "warning");
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
        failureLabel: "Image failed",
      });
      if (window.AIBillingGuard?.handleBillingResponse({ status: result.status }, result.data)) {
        return;
      }
      if (!result.ok) {
        ui.showMessage(result.error, "error");
        if (status) status.textContent = result.error;
        return;
      }

      if (img && result.data?.image_url) {
        img.src = result.data.image_url;
        img.classList.remove("is-hidden");
      }
      if (placeholder) placeholder.classList.add("is-hidden");
      if (status) status.textContent = "Image saved for this location.";
      ui.showMessage("Image created.", "success");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();
