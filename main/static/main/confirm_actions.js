(() => {
  document.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-confirm]");
    if (!button) return;
    if (button.disabled) return;

    const message = button.getAttribute("data-confirm") || "Are you sure?";
    const ok = window.confirm(message);
    if (!ok) {
      event.preventDefault();
      event.stopPropagation();
    }
  });
})();
