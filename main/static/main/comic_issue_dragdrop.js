(() => {
  const initializeIssueDragDrop = () => {
    const board = document.querySelector("[data-issue-swap-url]");
    if (!(board instanceof HTMLElement)) return;

    const swapUrl = board.getAttribute("data-issue-swap-url") || "";
    const ui = window.AppUI;
    if (!swapUrl || !ui) return;

    const csrfToken = ui.getCsrfToken();
    let draggedIssueId = null;
    let draggedCard = null;

    const getIssueCard = (event) => event.target?.closest?.(".comic-issue-card");

    const clearDragState = () => {
      document.querySelectorAll(".comic-issue-card.is-issue-dragging").forEach((card) => {
        card.classList.remove("is-issue-dragging");
      });
      document.querySelectorAll(".comic-issue-card.issue-drop-over").forEach((card) => {
        card.classList.remove("issue-drop-over");
      });
    };

    const submitSwap = async ({ issueId, targetIssueId }) => {
      const params = new URLSearchParams();
      params.set("issue_id", issueId);
      params.set("target_issue_id", targetIssueId);

      const result = await ui.postFormUrlEncoded({
        url: swapUrl,
        params,
        csrfToken,
        failureLabel: "Issue swap failed",
      });
      if (!result.ok) {
        ui.showMessage(result.error, "error", 3000);
        return;
      }

      ui.storeMessage("Issues swapped.", "success", 2500);
      window.location.reload();
    };

    document.addEventListener("dragstart", (event) => {
      const card = getIssueCard(event);
      if (!(card instanceof HTMLElement) || card.getAttribute("draggable") !== "true") return;
      if (event.target?.closest?.("a, button, input, textarea, select")) {
        event.preventDefault();
        return;
      }

      draggedIssueId = card.dataset.issueId || "";
      if (!draggedIssueId) return;

      draggedCard = card;
      card.classList.add("is-issue-dragging");
      try {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedIssueId);
      } catch (_error) {
        // Ignore browsers that restrict DataTransfer writes.
      }
    });

    document.addEventListener("dragend", () => {
      draggedIssueId = null;
      draggedCard = null;
      clearDragState();
    });

    document.addEventListener("dragover", (event) => {
      if (!draggedIssueId) return;
      const card = getIssueCard(event);
      if (!(card instanceof HTMLElement) || card === draggedCard || card.dataset.issueId === draggedIssueId) return;

      event.preventDefault();
      clearDragState();
      draggedCard?.classList.add("is-issue-dragging");
      card.classList.add("issue-drop-over");
      try {
        event.dataTransfer.dropEffect = "move";
      } catch (_error) {
        // no-op
      }
    });

    document.addEventListener("drop", (event) => {
      if (!draggedIssueId) return;
      const card = getIssueCard(event);
      if (!(card instanceof HTMLElement)) return;

      event.preventDefault();
      const targetIssueId = card.dataset.issueId || "";
      if (!targetIssueId || targetIssueId === draggedIssueId) return;

      submitSwap({ issueId: draggedIssueId, targetIssueId });
      draggedIssueId = null;
      draggedCard = null;
      clearDragState();
    });
  };

  if (document.readyState === "loading" || !window.AppUI) {
    document.addEventListener("DOMContentLoaded", initializeIssueDragDrop);
  } else {
    initializeIssueDragDrop();
  }
})();
