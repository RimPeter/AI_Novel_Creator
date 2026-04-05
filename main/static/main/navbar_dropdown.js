document.addEventListener("DOMContentLoaded", () => {
  const dropdowns = Array.from(document.querySelectorAll(".navbar .nav-dropdown"));

  if (dropdowns.length < 2) {
    return;
  }

  const closeOthers = (activeDropdown) => {
    dropdowns.forEach((dropdown) => {
      if (dropdown !== activeDropdown) {
        dropdown.removeAttribute("open");
      }
    });
  };

  dropdowns.forEach((dropdown) => {
    dropdown.addEventListener("toggle", () => {
      if (dropdown.open) {
        closeOthers(dropdown);
      }
    });
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".navbar")) {
      dropdowns.forEach((dropdown) => dropdown.removeAttribute("open"));
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      dropdowns.forEach((dropdown) => dropdown.removeAttribute("open"));
    }
  });
});
