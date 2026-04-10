(() => {
  const editor = document.querySelector("[data-draft-editor]");
  if (!editor) return;
  const scrollKey = `scene-draft-scroll:${window.location.pathname}`;

  const textarea = editor.querySelector("textarea.draft-input");
  const highlight = editor.querySelector(".draft-highlight");
  const synonymBtn = document.getElementById("draft-synonym-btn");
  if (!textarea || !highlight) return;

  const initialRegeneratedRangesRaw = editor.getAttribute("data-regenerated-ranges") || "";
  const synonymsUrl = editor.getAttribute("data-synonyms-url") || "";
  const synonymPopover = document.createElement("div");
  synonymPopover.className = "draft-synonym-popover";
  editor.appendChild(synonymPopover);

  let regeneratedRanges = [];
  let synonymMode = false;
  let activeWordElement = null;
  let hoverRequestId = 0;

  const synonymCache = new Map();

  const escapeHtml = (value) =>
    value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const normalizeLookupWord = (value) =>
    String(value || "")
      .toLowerCase()
      .replace(/^[^a-z]+|[^a-z]+$/g, "");

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

  const buildWordSpans = (text) => {
    const pattern = /[A-Za-z][A-Za-z'-]{1,}/g;
    let result = "";
    let lastIndex = 0;
    let match;

    while ((match = pattern.exec(text)) !== null) {
      const word = match[0];
      const normalized = normalizeLookupWord(word);
      result += escapeHtml(text.slice(lastIndex, match.index));
      if (normalized) {
        result += `<span class="draft-synonym-word" data-word="${escapeHtml(normalized)}">${escapeHtml(word)}</span>`;
      } else {
        result += escapeHtml(word);
      }
      lastIndex = pattern.lastIndex;
    }

    result += escapeHtml(text.slice(lastIndex));
    return result;
  };

  const buildHighlightedSegment = (text, classes) => {
    const content = synonymMode ? buildWordSpans(text) : escapeHtml(text);
    return classes.length ? `<span class="${classes.join(" ")}">${content}</span>` : content;
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

      result += buildHighlightedSegment(text.slice(start, end), classes);
    }

    return result;
  };

  const setPopoverContent = (word, state, synonyms = []) => {
    let body = "";

    if (state === "loading") {
      body = '<div class="draft-synonym-status">Loading similar words...</div>';
    } else if (state === "ready" && synonyms.length) {
      body = `<div class="draft-synonym-list">${synonyms
        .map((item) => `<span class="draft-synonym-chip">${escapeHtml(item)}</span>`)
        .join("")}</div>`;
    } else {
      body = '<div class="draft-synonym-status">No close synonyms found.</div>';
    }

    synonymPopover.innerHTML = `
      <div class="draft-synonym-title">${escapeHtml(word)}</div>
      ${body}
    `;
  };

  const hideSynonymPopover = () => {
    hoverRequestId += 1;
    activeWordElement = null;
    synonymPopover.classList.remove("is-visible");
    synonymPopover.innerHTML = "";
  };

  const positionSynonymPopover = (target) => {
    if (!target || !synonymPopover.classList.contains("is-visible")) return;

    const editorRect = editor.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const popoverWidth = synonymPopover.offsetWidth || 240;
    const popoverHeight = synonymPopover.offsetHeight || 0;
    const padding = 12;

    let left = targetRect.left - editorRect.left + targetRect.width / 2 - popoverWidth / 2;
    left = Math.max(padding, Math.min(left, Math.max(padding, editor.clientWidth - popoverWidth - padding)));

    let top = targetRect.top - editorRect.top - popoverHeight - 10;
    if (top < padding) {
      top = targetRect.bottom - editorRect.top + 10;
    }
    top = Math.max(padding, Math.min(top, Math.max(padding, editor.clientHeight - popoverHeight - padding)));

    synonymPopover.style.left = `${left}px`;
    synonymPopover.style.top = `${top}px`;
  };

  const fetchSynonyms = (word) => {
    if (!synonymsUrl) return Promise.resolve([]);
    if (synonymCache.has(word)) return synonymCache.get(word);

    const requestUrl = new URL(synonymsUrl, window.location.origin);
    requestUrl.searchParams.set("word", word);

    const request = fetch(requestUrl.toString(), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (!response.ok || !data || data.ok !== true || !Array.isArray(data.synonyms)) return [];

        const seen = new Set();
        return data.synonyms
          .map((item) => String(item || "").trim())
          .filter(Boolean)
          .filter((item) => normalizeLookupWord(item) !== word)
          .filter((item) => {
            const normalized = normalizeLookupWord(item);
            if (!normalized || seen.has(normalized)) return false;
            seen.add(normalized);
            return true;
          })
          .slice(0, 8);
      })
      .catch(() => []);

    synonymCache.set(word, request);
    return request;
  };

  const showSynonymPopover = async (target) => {
    const lookupWord = normalizeLookupWord(target?.dataset?.word || "");
    const labelWord = (target?.textContent || lookupWord || "").trim();
    if (!lookupWord) return;

    activeWordElement = target;
    const requestId = ++hoverRequestId;

    setPopoverContent(labelWord, "loading");
    synonymPopover.classList.add("is-visible");
    positionSynonymPopover(target);

    const synonyms = await fetchSynonyms(lookupWord);
    if (!synonymMode || requestId !== hoverRequestId || activeWordElement !== target) return;

    setPopoverContent(labelWord, synonyms.length ? "ready" : "empty", synonyms);
    positionSynonymPopover(target);
  };

  const updateHighlight = () => {
    hideSynonymPopover();
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

  const setSynonymMode = (enabled) => {
    synonymMode = Boolean(enabled && synonymBtn);
    editor.classList.toggle("draft-editor-synonym-mode", synonymMode);
    if (synonymBtn) {
      synonymBtn.setAttribute("aria-pressed", synonymMode ? "true" : "false");
    }
    if (synonymMode) {
      textarea.blur();
    }
    updateHighlight();
    syncScroll();
  };

  const form = textarea.closest("form");
  if (form) {
    form.addEventListener("submit", (event) => {
      const action = event.submitter?.value || "";
      if (["structurize", "render", "reshuffle"].includes(action) && window.AIBillingGuard?.redirectToBillingIfNeeded(editor)) {
        event.preventDefault();
        return;
      }
      if (action !== "reshuffle") return;
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
  textarea.addEventListener("scroll", () => {
    hideSynonymPopover();
    syncScroll();
  });

  if (synonymBtn) {
    synonymBtn.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    synonymBtn.addEventListener("click", () => {
      setSynonymMode(!synonymMode);
    });
  }

  highlight.addEventListener("mouseover", (event) => {
    if (!synonymMode) return;
    const wordEl = event.target.closest(".draft-synonym-word");
    if (!wordEl || !highlight.contains(wordEl) || wordEl === activeWordElement) return;
    showSynonymPopover(wordEl);
  });

  highlight.addEventListener("mouseleave", () => {
    if (!synonymMode) return;
    hideSynonymPopover();
  });

  highlight.addEventListener(
    "wheel",
    (event) => {
      if (!synonymMode) return;
      textarea.scrollTop += event.deltaY;
      textarea.scrollLeft += event.deltaX;
      hideSynonymPopover();
      syncScroll();
      event.preventDefault();
    },
    { passive: false },
  );

  window.addEventListener("resize", () => {
    if (synonymMode && activeWordElement) {
      positionSynonymPopover(activeWordElement);
    }
  });

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
