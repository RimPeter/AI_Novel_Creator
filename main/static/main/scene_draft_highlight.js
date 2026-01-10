(() => {
  const editor = document.querySelector("[data-draft-editor]");
  if (!editor) return;

  const textarea = editor.querySelector("textarea.draft-input");
  const highlight = editor.querySelector(".draft-highlight");
  if (!textarea || !highlight) return;

  const escapeHtml = (value) =>
    value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const buildHighlighted = (text) => {
    const pattern = /\{[^{}]*\}/g;
    let result = "";
    let lastIndex = 0;
    let match;

    while ((match = pattern.exec(text)) !== null) {
      const start = match.index;
      const end = pattern.lastIndex;
      result += escapeHtml(text.slice(lastIndex, start));
      result += `<span class="draft-highlight-token">${escapeHtml(match[0])}</span>`;
      lastIndex = end;
    }
    result += escapeHtml(text.slice(lastIndex));
    return result;
  };

  const updateHighlight = () => {
    const value = textarea.value || "";
    if (!value.trim()) {
      const placeholder = textarea.getAttribute("data-placeholder") || "";
      highlight.innerHTML = placeholder
        ? `<span class="draft-placeholder">${escapeHtml(placeholder)}</span>`
        : "";
      return;
    }
    highlight.innerHTML = buildHighlighted(value);
  };

  const syncScroll = () => {
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
  };

  textarea.addEventListener("input", updateHighlight);
  textarea.addEventListener("scroll", syncScroll);

  updateHighlight();
  syncScroll();
})();
