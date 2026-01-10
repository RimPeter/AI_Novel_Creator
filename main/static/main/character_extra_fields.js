(() => {
  const container = document.getElementById("character-extra-rows");
  const addBtn = document.getElementById("add-character-field");
  if (!container || !addBtn) return;

  const makeRow = () => {
    const row = document.createElement("div");
    row.className = "object-row";
    row.innerHTML = `
      <input class="form-control" name="extra_key" type="text" placeholder="Field name" />
      <textarea class="form-control" name="extra_value" rows="2" placeholder="Field value"></textarea>
      <button class="btn btn-secondary btn-sm remove-extra-row" type="button">Remove</button>
    `;
    return row;
  };

  const removeRow = (btn) => {
    const row = btn.closest(".object-row");
    if (!row) return;
    row.remove();
    if (!container.querySelector(".object-row")) {
      container.appendChild(makeRow());
    }
  };

  addBtn.addEventListener("click", () => {
    container.appendChild(makeRow());
    const inputs = container.querySelectorAll('input[name="extra_key"]');
    const last = inputs[inputs.length - 1];
    if (last) last.focus();
  });

  container.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".remove-extra-row");
    if (!btn) return;
    removeRow(btn);
  });
})();
