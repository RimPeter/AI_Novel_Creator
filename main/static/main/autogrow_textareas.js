(() => {
  const textareas = Array.from(document.querySelectorAll("textarea[data-autogrow='true']"));
  if (!textareas.length) return;

  const resize = (textarea) => {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;

    const editor = textarea.closest("[data-draft-editor]");
    if (editor) {
      const highlight = editor.querySelector(".draft-highlight");
      if (highlight) {
        highlight.style.height = `${textarea.scrollHeight}px`;
      }
    }
  };

  const resizeAll = () => {
    textareas.forEach((textarea) => resize(textarea));
  };

  textareas.forEach((textarea) => {
    textarea.addEventListener("input", () => resize(textarea));
    textarea.addEventListener("change", () => resize(textarea));
  });

  window.addEventListener("load", resizeAll);
  resizeAll();
})();
