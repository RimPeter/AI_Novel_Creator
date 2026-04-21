(() => {
  const FLASH_STORAGE_KEY = "app-ui-flash-message";
  const DEFAULT_MESSAGE_TIMEOUT_MS = 7000;
  const LOADING_MESSAGE_TEXT = "Loading...";
  const LOADING_MESSAGE_LEVEL = "info";
  const LOADING_MESSAGE_DELAY_MS = 180;
  const LOADING_MESSAGE_TIMEOUT_MS = 60000;
  let loadingCount = 0;
  let loadingTimerId = null;
  let loadingItem = null;

  const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  };

  const getCsrfToken = () => getCookie("csrftoken");

  const ensureMessageList = () => {
    const existing = document.querySelector(".messages");
    if (existing) return existing;

    const list = document.createElement("ul");
    list.className = "messages";
    list.setAttribute("aria-live", "polite");
    document.body.appendChild(list);
    return list;
  };

  const removeMessageAfterDelay = (item, timeoutMs = DEFAULT_MESSAGE_TIMEOUT_MS) => {
    const delay = Number(timeoutMs) || DEFAULT_MESSAGE_TIMEOUT_MS;
    window.setTimeout(() => item.remove(), delay);
  };

  const showMessage = (text, level = "info", timeoutMs = DEFAULT_MESSAGE_TIMEOUT_MS) => {
    if (!String(text || "").trim()) return;
    const list = ensureMessageList();
    const item = document.createElement("li");
    item.className = `message message-${level}`;
    item.textContent = text;
    list.appendChild(item);
    removeMessageAfterDelay(item, timeoutMs);
  };

  const initializeExistingMessages = () => {
    document.querySelectorAll(".messages .message").forEach((item) => {
      if (item.dataset.autoDismissInitialized === "true") return;
      item.dataset.autoDismissInitialized = "true";
      removeMessageAfterDelay(item, DEFAULT_MESSAGE_TIMEOUT_MS);
    });
  };

  const showLoading = (text = LOADING_MESSAGE_TEXT) => {
    loadingCount += 1;
    if (loadingItem || loadingTimerId !== null) return;

    loadingTimerId = window.setTimeout(() => {
      loadingTimerId = null;
      if (loadingCount <= 0 || loadingItem) return;
      const list = ensureMessageList();
      const item = document.createElement("li");
      item.className = `message message-${LOADING_MESSAGE_LEVEL} message-loading`;
      item.textContent = String(text || LOADING_MESSAGE_TEXT).trim() || LOADING_MESSAGE_TEXT;
      item.dataset.loadingToast = "true";
      list.appendChild(item);
      loadingItem = item;
      window.setTimeout(() => {
        if (loadingItem === item) {
          item.remove();
          loadingItem = null;
        }
      }, LOADING_MESSAGE_TIMEOUT_MS);
    }, LOADING_MESSAGE_DELAY_MS);
  };

  const hideLoading = () => {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount > 0) return;
    if (loadingTimerId !== null) {
      window.clearTimeout(loadingTimerId);
      loadingTimerId = null;
    }
    if (loadingItem) {
      loadingItem.remove();
      loadingItem = null;
    }
  };

  const storeMessage = (text, level = "info", timeoutMs = DEFAULT_MESSAGE_TIMEOUT_MS) => {
    const payload = {
      text: String(text || "").trim(),
      level: String(level || "info").trim() || "info",
      timeoutMs: Number(timeoutMs) || DEFAULT_MESSAGE_TIMEOUT_MS,
    };
    if (!payload.text) return;
    try {
      window.sessionStorage.setItem(FLASH_STORAGE_KEY, JSON.stringify(payload));
    } catch (_error) {
      // Ignore storage failures and fall back to in-page messages only.
    }
  };

  const consumeStoredMessage = () => {
    try {
      const raw = window.sessionStorage.getItem(FLASH_STORAGE_KEY);
      if (!raw) return;
      window.sessionStorage.removeItem(FLASH_STORAGE_KEY);
      const payload = JSON.parse(raw);
      showMessage(
        payload?.text || "",
        payload?.level || "info",
        payload?.timeoutMs || DEFAULT_MESSAGE_TIMEOUT_MS,
      );
    } catch (_error) {
      try {
        window.sessionStorage.removeItem(FLASH_STORAGE_KEY);
      } catch (_nestedError) {
        // Ignore storage cleanup failures.
      }
    }
  };

  const postFormUrlEncoded = async ({ url, params, csrfToken = "", failureLabel = "Request failed" }) => {
    showLoading();
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        credentials: "same-origin",
        body: params.toString(),
      });

      const data = await response.json().catch(() => null);
      if (!response.ok || !data || data.ok !== true) {
        return {
          ok: false,
          data,
          status: response.status,
          error: data?.error || `${failureLabel} (${response.status})`,
        };
      }

      return { ok: true, data, status: response.status, error: "" };
    } catch (error) {
      return {
        ok: false,
        data: null,
        status: 0,
        error: `${failureLabel}: ${error?.message || error}`,
      };
    } finally {
      hideLoading();
    }
  };

  window.AppUI = {
    getCookie,
    getCsrfToken,
    showLoading,
    hideLoading,
    showMessage,
    storeMessage,
    postFormUrlEncoded,
  };

  initializeExistingMessages();
  consumeStoredMessage();
})();
