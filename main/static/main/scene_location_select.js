(() => {
  const section = document.querySelector("[data-location-create-url]");
  if (!section) return;

  const createUrl = section.getAttribute("data-location-create-url");
  const select = section.querySelector('select[name="location"]');
  if (!createUrl || !select) return;

  select.addEventListener("change", () => {
    if (select.value === "__create__") {
      window.location.href = createUrl;
    }
  });
})();

