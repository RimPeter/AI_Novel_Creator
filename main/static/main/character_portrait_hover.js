(() => {
  const list = document.querySelector(".multi-select-list");
  if (!list) return;

  const labels = list.querySelectorAll(".character-option[data-portrait-url]");
  if (!labels.length) return;

  const tooltip = document.createElement("div");
  tooltip.className = "portrait-tooltip";
  const img = document.createElement("img");
  img.alt = "Character portrait";
  tooltip.appendChild(img);
  document.body.appendChild(tooltip);

  const showTooltip = (url) => {
    if (!url) return false;
    img.src = url;
    tooltip.classList.add("is-visible");
    return true;
  };

  const hideTooltip = () => {
    tooltip.classList.remove("is-visible");
  };

  const positionTooltip = (x, y) => {
    const padding = 12;
    const width = tooltip.offsetWidth || 180;
    const height = tooltip.offsetHeight || 220;
    let left = x + 16;
    let top = y + 16;

    if (left + width + padding > window.innerWidth) {
      left = x - width - 16;
    }
    if (top + height + padding > window.innerHeight) {
      top = window.innerHeight - height - padding;
    }
    if (top < padding) top = padding;
    if (left < padding) left = padding;

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  };

  labels.forEach((label) => {
    const url = label.dataset.portraitUrl;
    if (!url) return;

    label.addEventListener("mouseenter", (event) => {
      if (!showTooltip(url)) return;
      positionTooltip(event.clientX, event.clientY);
    });

    label.addEventListener("mousemove", (event) => {
      if (!tooltip.classList.contains("is-visible")) return;
      positionTooltip(event.clientX, event.clientY);
    });

    label.addEventListener("mouseleave", hideTooltip);
  });
})();
