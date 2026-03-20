(() => {
  const editor = document.querySelector("[data-draft-editor]");
  if (!editor) return;
  const scrollKey = `scene-draft-scroll:${window.location.pathname}`;

  const textarea = editor.querySelector("textarea.draft-input");
  const highlight = editor.querySelector(".draft-highlight");
  if (!textarea || !highlight) return;
  const initialRegeneratedRangesRaw = editor.getAttribute("data-regenerated-ranges") || "";
  let regeneratedRanges = [];

  const escapeHtml = (value) =>
    value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const parseRanges = (raw) =>
    String(raw || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [startRaw, endRaw] = part.split(":");
        const start = Number.parseInt(startRaw, 10);
        const end = Number.parseInt(endRaw, 10);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
        return { start, end };
      })
      .filter(Boolean);

  regeneratedRanges = parseRanges(initialRegeneratedRangesRaw);

  const getTokenMatches = (text) => {
    const pattern = /!\{[^{}]*\}!|\{[^{}]*\}/g;
    const matches = [];
    let match;

    while ((match = pattern.exec(text)) !== null) {
      matches.push({
        start: match.index,
        end: pattern.lastIndex,
        className: match[0].startsWith("!{")
          ? "draft-highlight-token draft-highlight-token-targeted"
          : "draft-highlight-token",
      });
    }

    return matches;
  };

  const buildHighlighted = (text) => {
    const tokenMatches = getTokenMatches(text);
    const boundaries = new Set([0, text.length]);
    for (const token of tokenMatches) {
      boundaries.add(token.start);
      boundaries.add(token.end);
    }
    for (const range of regeneratedRanges) {
      const start = Math.max(0, Math.min(text.length, range.start));
      const end = Math.max(0, Math.min(text.length, range.end));
      if (end <= start) continue;
      boundaries.add(start);
      boundaries.add(end);
    }

    const sortedBoundaries = Array.from(boundaries).sort((a, b) => a - b);
    let result = "";

    for (let i = 0; i < sortedBoundaries.length - 1; i += 1) {
      const start = sortedBoundaries[i];
      const end = sortedBoundaries[i + 1];
      if (end <= start) continue;

      const classes = [];
      const token = tokenMatches.find((item) => start >= item.start && end <= item.end);
      if (token) classes.push(token.className);
      const regenerated = regeneratedRanges.some((range) => start >= range.start && end <= range.end);
      if (regenerated) classes.push("draft-highlight-regenerated");

      const chunk = escapeHtml(text.slice(start, end));
      result += classes.length ? `<span class="${classes.join(" ")}">${chunk}</span>` : chunk;
    }

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

  const restoreScrollPosition = () => {
    try {
      const raw = window.sessionStorage.getItem(scrollKey);
      if (!raw) return;
      const data = JSON.parse(raw);
      window.sessionStorage.removeItem(scrollKey);
      if (data?.path && data.path !== window.location.pathname) return;
      const x = Number.parseInt(data?.x, 10);
      const y = Number.parseInt(data?.y, 10);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        window.scrollTo(x, y);
      }
    } catch (_) {
      window.sessionStorage.removeItem(scrollKey);
    }
  };

  const form = textarea.closest("form");
  if (form) {
    form.addEventListener("submit", (event) => {
      if (event.submitter?.value !== "reshuffle") return;
      try {
        window.sessionStorage.setItem(
          scrollKey,
          JSON.stringify({
            path: window.location.pathname,
            x: window.scrollX,
            y: window.scrollY,
          }),
        );
      } catch (_) {}
    });
  }

  textarea.addEventListener("input", () => {
    if (regeneratedRanges.length) {
      regeneratedRanges = [];
      editor.setAttribute("data-regenerated-ranges", "");
    }
    updateHighlight();
  });
  textarea.addEventListener("scroll", syncScroll);

  updateHighlight();
  syncScroll();
  restoreScrollPosition();

  if (regeneratedRanges.length) {
    if (window.history?.replaceState) {
      const url = new URL(window.location.href);
      url.searchParams.delete("hl");
      window.history.replaceState({}, "", url.toString());
    }
  }
})();
