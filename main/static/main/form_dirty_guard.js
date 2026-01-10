(() => {
  const form = document.querySelector("form[data-dirty-guard='true']");
  if (!form) return;

  const serializeForm = () => {
    const params = new URLSearchParams(new FormData(form));
    return params.toString();
  };

  let initial = serializeForm();
  let isSubmitting = false;

  const isDirty = () => serializeForm() !== initial;

  const handleBeforeUnload = (event) => {
    if (isSubmitting || !isDirty()) return;
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
      if (isSubmitting || !isDirty()) return;
      const link = event.target?.closest?.("a[href]");
      if (!link) return;
      if (link.hasAttribute("download") || link.target === "_blank") return;

      const nextUrl = new URL(link.href, window.location.href);
      if (nextUrl.origin !== window.location.origin) return;

      const ok = window.confirm("You have unsaved changes. Leave without saving?");
      if (!ok) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );
})();
