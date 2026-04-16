(() => {
  const REQUIRED_PLAN_MESSAGE = "An active plan is required to generate text and use tokens.";

  const readConfig = (element) => ({
    billingEnabled: element?.getAttribute("data-billing-enabled") === "true",
    aiBillingUrl: element?.getAttribute("data-ai-billing-url") || "",
    hasActivePlan: element?.getAttribute("data-has-active-plan") === "true",
  });

  const redirectToBillingIfNeeded = (element) => {
    const { billingEnabled, aiBillingUrl, hasActivePlan } = readConfig(element);
    if (!billingEnabled || hasActivePlan || !aiBillingUrl) return false;
    window.AppUI?.storeMessage(REQUIRED_PLAN_MESSAGE, "error");
    window.location.assign(aiBillingUrl);
    return true;
  };

  const handleBillingResponse = (response, data) => {
    if (response?.status !== 402 || !data?.billing_url) return false;
    window.AppUI?.storeMessage(data?.error || REQUIRED_PLAN_MESSAGE, "error");
    window.location.assign(data.billing_url);
    return true;
  };

  window.AIBillingGuard = {
    handleBillingResponse,
    redirectToBillingIfNeeded,
  };
})();
