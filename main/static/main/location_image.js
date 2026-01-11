(() => {
  const section = document.querySelector("[data-location-image-url]");
  const btn = document.getElementById("location-image-btn");
  if (!section || !btn) return;

  const url = section.getAttribute("data-location-image-url");
  const form = section.querySelector("form");
  if (!url || !form) return;

  const img = document.getElementById("location-image-img");
  const placeholder = document.getElementById("location-image-placeholder");
  const status = document.getElementById("location-image-status");

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

  const getFieldValue = (name) => (form.querySelector(`[name="${name}"]`)?.value || "").trim();

  btn.addEventListener("click", async () => {
    if (!getFieldValue("name")) {
      showMessage("Add a name first, then create an image.", "warning");
      return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Creating...";

    try {
      const params = new URLSearchParams(new FormData(form));
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
        const errorText = data?.error || `Image failed (${res.status})`;
        showMessage(errorText, "error");
        if (status) status.textContent = errorText;
        return;
      }

      if (img && data.image_url) {
        img.src = data.image_url;
        img.classList.remove("is-hidden");
      }
      if (placeholder) placeholder.classList.add("is-hidden");
      if (status) status.textContent = "Image saved for this location.";
      showMessage("Image created.", "success");
    } catch (e) {
      showMessage(`Request failed: ${e?.message || e}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();
