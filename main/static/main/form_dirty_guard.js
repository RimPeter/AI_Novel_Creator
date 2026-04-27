(() => {
  const form = document.querySelector("form[data-dirty-guard='true']");
  if (!form) return;

  const serializeForm = () => {
    const params = new URLSearchParams(new FormData(form));
    return params.toString();
  };

  let initial = serializeForm();
  let isSubmitting = false;
  const alwaysPrompt = form.dataset.dirtyGuardAlways === "true";
  const autoSave = form.dataset.dirtyGuardAutosave === "true";
  let pendingHref = "";
  let toast = null;

  const isDirty = () => serializeForm() !== initial;
  const shouldPrompt = () => !isSubmitting && (alwaysPrompt || isDirty());
  const removeToast = () => {
    if (toast) {
      toast.remove();
      toast = null;
    }
  };

  const leaveWithoutSaving = () => {
    if (!pendingHref) return;
    isSubmitting = true;
    window.removeEventListener("beforeunload", handleBeforeUnload);
    window.location.href = pendingHref;
  };

  const savePage = () => {
    removeToast();
    isSubmitting = true;
    form.requestSubmit();
  };

  const autoSaveThenLeave = async (href) => {
    pendingHref = href;
    removeToast();
    isSubmitting = true;
    window.removeEventListener("beforeunload", handleBeforeUnload);

    try {
      const response = await fetch(form.action || window.location.href, {
        method: (form.method || "post").toUpperCase(),
        body: new FormData(form),
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-Comic-Page-Autosave": "true",
        },
        credentials: "same-origin",
      });

      if (!response.ok) {
        isSubmitting = false;
        window.addEventListener("beforeunload", handleBeforeUnload);
        showSaveToast(href);
        return;
      }

      initial = serializeForm();
      window.location.href = href;
    } catch (_error) {
      isSubmitting = false;
      window.addEventListener("beforeunload", handleBeforeUnload);
      showSaveToast(href);
    }
  };

  const showSaveToast = (href) => {
    pendingHref = href;
    removeToast();

    toast = document.createElement("div");
    toast.className = "dirty-guard-toast";
    toast.setAttribute("role", "alertdialog");
    toast.setAttribute("aria-live", "assertive");
    toast.setAttribute("aria-label", "Unsaved page changes");

    const body = document.createElement("div");
    body.className = "dirty-guard-toast-body";

    const title = document.createElement("strong");
    title.className = "dirty-guard-toast-title";
    title.textContent = "Save this page?";

    const copy = document.createElement("p");
    copy.className = "dirty-guard-toast-copy";
    copy.textContent = "You can save your changes before leaving, leave without saving, or stay on this page.";

    body.append(title, copy);

    const actions = document.createElement("div");
    actions.className = "dirty-guard-toast-actions";

    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "btn btn-primary btn-sm";
    saveButton.textContent = "Save";
    saveButton.addEventListener("click", savePage);

    const leaveButton = document.createElement("button");
    leaveButton.type = "button";
    leaveButton.className = "btn btn-secondary btn-sm";
    leaveButton.textContent = "Don't save";
    leaveButton.addEventListener("click", leaveWithoutSaving);

    const stayButton = document.createElement("button");
    stayButton.type = "button";
    stayButton.className = "btn btn-secondary btn-sm";
    stayButton.textContent = "Cancel";
    stayButton.addEventListener("click", removeToast);

    actions.append(saveButton, leaveButton, stayButton);
    toast.append(body, actions);
    document.body.appendChild(toast);
    saveButton.focus();
  };

  const handleBeforeUnload = (event) => {
    if (!shouldPrompt()) return;
    event.preventDefault();
    event.returnValue = "";
  };

  window.addEventListener("beforeunload", handleBeforeUnload);

  form.addEventListener("submit", () => {
    isSubmitting = true;
  });

  document.addEventListener(
    "click",
    (event) => {
      if (!shouldPrompt()) return;
      const link = event.target?.closest?.("a[href]");
      if (!link) return;
      if (link.hasAttribute("download") || link.target === "_blank") return;

      const nextUrl = new URL(link.href, window.location.href);
      if (nextUrl.origin !== window.location.origin) return;

      event.preventDefault();
      event.stopPropagation();
      if (autoSave) {
        autoSaveThenLeave(nextUrl.href);
        return;
      }
      showSaveToast(nextUrl.href);
    },
    true
  );
})();
