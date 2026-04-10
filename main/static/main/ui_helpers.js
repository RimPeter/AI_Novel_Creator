(() => {
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
    const main = document.querySelector("main.wrap") || document.body;
    main.insertBefore(list, main.firstChild);
    return list;
  };

  const showMessage = (text, level = "info", timeoutMs = 3000) => {
    const list = ensureMessageList();
    const item = document.createElement("li");
    item.className = `message message-${level}`;
    item.textContent = text;
    list.appendChild(item);
    window.setTimeout(() => item.remove(), timeoutMs);
  };

  const postFormUrlEncoded = async ({ url, params, csrfToken = "", failureLabel = "Request failed" }) => {
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
    }
  };

  window.AppUI = {
    getCookie,
    getCsrfToken,
    showMessage,
    postFormUrlEncoded,
  };
})();
