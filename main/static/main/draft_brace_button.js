(() => {
  const btn = document.getElementById("draft-brace-btn");
  const textarea = document.querySelector(".draft-input");
  if (!btn || !textarea) return;

  const updateValue = (value, start, end) => {
    textarea.value = value;
    textarea.focus();
    if (typeof start === "number" && typeof end === "number") {
      textarea.setSelectionRange(start, end);
    }
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  btn.addEventListener("click", () => {
    const value = textarea.value || "";
    const start = textarea.selectionStart ?? value.length;
    const end = textarea.selectionEnd ?? value.length;

    if (start !== end) {
      const next =
        value.slice(0, start) + "{" + value.slice(start, end) + "}" + value.slice(end);
      updateValue(next, start + 1, end + 1);
      return;
    }

    const next = value.slice(0, start) + "{}" + value.slice(end);
    updateValue(next, start + 1, start + 1);
  });
})();
