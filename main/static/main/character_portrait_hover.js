(() => {
  const targets = [
    ...document.querySelectorAll(".multi-select-list .character-option[data-portrait-url]"),
    ...document.querySelectorAll(".character-card[data-portrait-url]"),
  ];
  if (!targets.length) return;

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

  targets.forEach((target) => {
    const url = target.dataset.portraitUrl;
    if (!url) return;

    target.addEventListener("mouseenter", (event) => {
      if (!showTooltip(url)) return;
      positionTooltip(event.clientX, event.clientY);
    });

    target.addEventListener("mousemove", (event) => {
      if (!tooltip.classList.contains("is-visible")) return;
      positionTooltip(event.clientX, event.clientY);
    });

    target.addEventListener("mouseleave", hideTooltip);
  });
})();
