(() => {
  window.DynamicRows?.initDynamicRows({
    containerId: "object-rows",
    addButtonId: "add-object-row",
    removeButtonClass: "remove-object-row",
    keyName: "object_key",
    valueName: "object_value",
    keyPlaceholder: "Object (key)",
    valuePlaceholder: "Attributes (value)",
  });
})();
