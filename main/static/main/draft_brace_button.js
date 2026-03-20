(() => {
  const btn = document.getElementById("draft-brace-btn");
  const targetBtn = document.getElementById("draft-target-btn");
  const unbraceBtn = document.getElementById("draft-unbrace-btn");
  const textarea = document.querySelector(".draft-input");
  if (!targetBtn || !unbraceBtn || !textarea) return;

  const updateValue = (value, start, end) => {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const editorScrollTop = textarea.scrollTop;
    const editorScrollLeft = textarea.scrollLeft;
    textarea.value = value;
    if (typeof textarea.focus === "function") {
      try {
        textarea.focus({ preventScroll: true });
      } catch (_) {
        textarea.focus();
      }
    }
    if (typeof start === "number" && typeof end === "number") {
      textarea.setSelectionRange(start, end);
    }
    textarea.scrollTop = editorScrollTop;
    textarea.scrollLeft = editorScrollLeft;
    window.scrollTo(scrollX, scrollY);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const stripMarkers = (value) => String(value || "").replace(/!\{|}!|[{}]/g, "");

  const expandSelectionToWrappedRegion = (value, start, end) => {
    if (start === end) return { start, end };

    let nextStart = start;
    let nextEnd = end;
    let changed = true;

    while (changed) {
      changed = false;

      if (
        nextStart > 1 &&
        nextEnd + 1 < value.length &&
        value.slice(nextStart - 2, nextStart) === "!{" &&
        value.slice(nextEnd, nextEnd + 2) === "}!"
      ) {
        nextStart -= 2;
        nextEnd += 2;
        changed = true;
        continue;
      }

      if (
        nextStart > 0 &&
        nextEnd < value.length &&
        value[nextStart - 1] === "{" &&
        value[nextEnd] === "}" &&
        (nextStart < 2 || value[nextStart - 2] !== "!") &&
        (nextEnd + 1 >= value.length || value[nextEnd + 1] !== "!")
      ) {
        nextStart -= 1;
        nextEnd += 1;
        changed = true;
      }
    }

    return { start: nextStart, end: nextEnd };
  };

  const wrapSelection = (prefix, suffix) => {
    const value = textarea.value || "";
    const start = textarea.selectionStart ?? value.length;
    const end = textarea.selectionEnd ?? value.length;

    if (start !== end) {
      const normalized = expandSelectionToWrappedRegion(value, start, end);
      const cleaned = stripMarkers(value.slice(normalized.start, normalized.end));
      const next =
        value.slice(0, normalized.start) + prefix + cleaned + suffix + value.slice(normalized.end);
      updateValue(
        next,
        normalized.start + prefix.length,
        normalized.start + prefix.length + cleaned.length,
      );
      return;
    }

    const next = value.slice(0, start) + prefix + suffix + value.slice(end);
    updateValue(next, start + prefix.length, start + prefix.length);
  };

  if (btn) {
    btn.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    btn.addEventListener("click", () => {
      wrapSelection("{", "}");
    });
  }

  targetBtn.addEventListener("mousedown", (event) => {
    event.preventDefault();
  });
  targetBtn.addEventListener("click", () => {
    wrapSelection("!{", "}!");
  });

  unbraceBtn.addEventListener("mousedown", (event) => {
    event.preventDefault();
  });
  unbraceBtn.addEventListener("click", () => {
    const value = textarea.value || "";
    const start = textarea.selectionStart ?? value.length;
    const end = textarea.selectionEnd ?? value.length;

    if (start !== end) {
      let next = value;
      let nextStart = start;
      let nextEnd = end;

      const hasTargetWrapper =
        start > 1 &&
        end + 1 < value.length &&
        value.slice(start - 2, start) === "!{" &&
        value.slice(end, end + 2) === "}!";

      const hasPlainWrapper =
        start > 0 &&
        end < value.length &&
        value[start - 1] === "{" &&
        value[end] === "}" &&
        (start < 2 || value[start - 2] !== "!") &&
        (end + 1 >= value.length || value[end + 1] !== "!");

      if (hasTargetWrapper) {
        next = value.slice(0, start - 2) + value.slice(start, end) + value.slice(end + 2);
        nextStart = start - 2;
        nextEnd = end - 2;
      } else if (hasPlainWrapper) {
        next = value.slice(0, start - 1) + value.slice(start, end) + value.slice(end + 1);
        nextStart = start - 1;
        nextEnd = end - 1;
      } else {
        const cleaned = stripMarkers(value.slice(start, end));
        next = value.slice(0, start) + cleaned + value.slice(end);
        nextEnd = start + cleaned.length;
      }

      updateValue(next, nextStart, nextEnd);
      return;
    }

    const beforeCursor = value.slice(0, start);
    const next = stripMarkers(value);
    const nextCursor = stripMarkers(beforeCursor).length;
    updateValue(next, nextCursor, nextCursor);
  });
})();
