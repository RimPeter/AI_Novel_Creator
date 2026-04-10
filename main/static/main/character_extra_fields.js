(() => {
  window.DynamicRows?.initDynamicRows({
    containerId: "character-extra-rows",
    addButtonId: "add-character-field",
    removeButtonClass: "remove-extra-row",
    keyName: "extra_key",
    valueName: "extra_value",
    keyPlaceholder: "Field name",
    valuePlaceholder: "Field value",
  });
})();
