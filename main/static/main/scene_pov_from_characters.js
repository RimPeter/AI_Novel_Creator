(() => {
  const form = document.querySelector("form");
  const povSelect = document.querySelector("select.pov-select");
  if (!form || !povSelect) return;

  const getCheckedNames = () =>
    Array.from(form.querySelectorAll('input[name="characters"]:checked'))
      .map((input) => input.getAttribute("data-character-name") || "")
      .map((name) => name.trim())
      .filter(Boolean);

  const buildOptions = () => {
    const names = getCheckedNames();
    const current = povSelect.value;

    povSelect.innerHTML = "";
    if (!names.length) {
      const opt = new Option("Select characters first", "");
      opt.disabled = false;
      opt.selected = true;
      povSelect.appendChild(opt);
      if (current) {
        const keep = new Option(`Current: ${current}`, current);
        keep.selected = true;
        povSelect.appendChild(keep);
      }
      return;
    }

    povSelect.appendChild(new Option("Select POV", ""));
    for (const name of names) {
      const opt = new Option(name, name);
      if (name === current) opt.selected = true;
      povSelect.appendChild(opt);
    }
    if (current && !names.includes(current)) {
      const keep = new Option(`Current: ${current}`, current);
      keep.selected = true;
      povSelect.appendChild(keep);
    }
  };

  buildOptions();
  form.addEventListener("change", (event) => {
    if (event.target?.name === "characters") {
      buildOptions();
    }
  });
})();
