(() => {
  const container = document.getElementById("object-rows");
  const addBtn = document.getElementById("add-object-row");
  if (!container || !addBtn) return;

  const makeRow = () => {
    const row = document.createElement("div");
    row.className = "object-row";
    row.innerHTML = `
      <input class="form-control" name="object_key" type="text" placeholder="Object (key)" />
      <textarea class="form-control" name="object_value" rows="2" placeholder="Attributes (value)"></textarea>
      <button class="btn btn-secondary btn-sm remove-object-row" type="button">Remove</button>
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
    const inputs = container.querySelectorAll('input[name="object_key"]');
    const last = inputs[inputs.length - 1];
    if (last) last.focus();
  });

  container.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".remove-object-row");
    if (!btn) return;
    removeRow(btn);
  });
})();

