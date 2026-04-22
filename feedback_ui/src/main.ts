/**
 * Feedback widget entry point.
 * Reads configuration from the <script> tag's data attributes and initializes the widget.
 *
 * Usage:
 *   <script src="feedback-widget.js" data-site="your-site-id" data-api="https://your-backend"></script>
 *
 * data-site: required — identifies which site this feedback is for
 * data-api:  optional — backend URL (defaults to the script's origin, or "" for same-origin in dev)
 */

import { createWidget } from "./widget";

// Extend window for global config fallback (useful for inline/eval injection)
declare global {
  interface Window {
    __feedbackWidgetConfig?: { siteId: string; apiBase: string };
  }
}

function init() {
  // Check for global config first (set before script loads), then fall back to data attributes
  if (window.__feedbackWidgetConfig) {
    createWidget(window.__feedbackWidgetConfig);
    return;
  }

  const scriptTag =
    document.currentScript ||
    document.querySelector('script[data-site][src*="feedback"]');

  const siteId = scriptTag?.getAttribute("data-site") || "default";
  const apiBase = scriptTag?.getAttribute("data-api") || "";

  createWidget({ siteId, apiBase });
}

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
