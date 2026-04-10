(() => {
  const initDynamicRows = ({
    containerId,
    addButtonId,
    removeButtonClass,
    keyName,
    valueName,
    keyPlaceholder,
    valuePlaceholder,
  }) => {
    const container = document.getElementById(containerId);
    const addBtn = document.getElementById(addButtonId);
    if (!container || !addBtn) return;

    const makeRow = () => {
      const row = document.createElement("div");
      row.className = "object-row";
      row.innerHTML = `
        <input class="form-control" name="${keyName}" type="text" placeholder="${keyPlaceholder}" />
        <textarea class="form-control" name="${valueName}" rows="2" placeholder="${valuePlaceholder}"></textarea>
        <button class="btn btn-secondary btn-sm ${removeButtonClass}" type="button">Remove</button>
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
      const inputs = container.querySelectorAll(`input[name="${keyName}"]`);
      const last = inputs[inputs.length - 1];
      if (last) last.focus();
    });

    container.addEventListener("click", (event) => {
      const btn = event.target?.closest?.(`.${removeButtonClass}`);
      if (!btn) return;
      removeRow(btn);
    });
  };

  window.DynamicRows = {
    initDynamicRows,
  };
})();
