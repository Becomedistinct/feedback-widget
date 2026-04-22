/**
 * Feedback widget — injects a hidden trigger link and a slide-in recording overlay
 * into the host page using Shadow DOM for style isolation.
 *
 * Desktop: screen + mic recording via getDisplayMedia.
 * Mobile: audio + periodic page screenshots via html2canvas.
 */

import { WIDGET_CSS } from "./styles";
import {
  startRecording,
  startMobileRecording,
  stopRecording,
  setStatusCallback,
  isMobileMode,
  getScreenshots,
  type RecorderState,
} from "./recorder";
import { uploadRecording, uploadMobileRecording } from "./uploader";

export interface WidgetConfig {
  siteId: string;
  apiBase: string;
}

type UIState = "idle" | "ready" | "recording" | "uploading" | "done" | "error";

/** True when getDisplayMedia is unavailable (mobile browsers). */
function isDesktopRecordingSupported(): boolean {
  return (
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getDisplayMedia === "function"
  );
}

export function createWidget(config: WidgetConfig) {
  const desktopMode = isDesktopRecordingSupported();

  // Create Shadow DOM host
  const host = document.createElement("div");
  host.id = "feedback-widget-host";
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });

  // Inject styles
  const style = document.createElement("style");
  style.textContent = WIDGET_CSS;
  shadow.appendChild(style);

  // State
  let uiState: UIState = "idle";
  let recordingBlob: Blob | null = null;
  let durationSeconds = 0;
  let uploadPercent = 0;
  let errorMessage = "";
  let screenshotCount = 0;
  let overlayMinimized = false;

  // DOM references
  const trigger = document.createElement("button");
  trigger.className = "fb-trigger";
  trigger.textContent = "Feedback";
  trigger.style.display = "none"; // hidden until user scrolls to bottom
  shadow.appendChild(trigger);

  // Show trigger only when user has scrolled to the very bottom of the page.
  // Uses the max of body/documentElement scrollHeight for cross-browser mobile reliability.
  function checkScrollBottom() {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const docHeight = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight
    );
    const isScrollable = docHeight > windowHeight + 100;
    // Within 60px of the bottom, OR the page is too short to scroll
    const atBottom = scrollTop + windowHeight >= docHeight - 60;
    trigger.style.display = (atBottom || !isScrollable) ? "" : "none";
  }
  window.addEventListener("scroll", checkScrollBottom, { passive: true });
  window.addEventListener("resize", checkScrollBottom, { passive: true });
  // Check on load in case page is already at bottom (short pages)
  checkScrollBottom();

  const overlay = document.createElement("div");
  overlay.className = "fb-overlay";
  shadow.appendChild(overlay);

  // Recorder status callback
  setStatusCallback((state: RecorderState) => {
    if (state.status === "recording") {
      durationSeconds = state.durationSeconds;
      screenshotCount = state.screenshotCount || 0;
      setUIState("recording");
    } else if (state.status === "stopped" && state.blob) {
      recordingBlob = state.blob;
      durationSeconds = state.durationSeconds;
      screenshotCount = state.screenshotCount || 0;
      // When recording stops, un-minimize and show upload progress
      overlayMinimized = false;
      overlay.classList.add("open");
      trigger.classList.remove("fb-trigger-recording");
      handleUpload();
    } else if (state.status === "error") {
      errorMessage = state.error || "Recording failed";
      overlayMinimized = false;
      overlay.classList.add("open");
      trigger.classList.remove("fb-trigger-recording");
      setUIState("error");
    }
  });

  function setUIState(newState: UIState) {
    uiState = newState;
    // Show the recording pill on the trigger while recording (even when minimized)
    if (newState === "recording") {
      trigger.style.display = "";
      trigger.classList.add("fb-trigger-recording");
      trigger.textContent = "● Stop Recording";
    } else {
      trigger.classList.remove("fb-trigger-recording");
      trigger.textContent = "Feedback";
      // Re-apply scroll visibility logic for non-recording states
      checkScrollBottom();
    }
    render();
  }

  function formatTime(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function render() {
    overlay.innerHTML = "";

    // Close button
    const closeBtn = document.createElement("button");
    closeBtn.className = "fb-close";
    closeBtn.textContent = "\u00d7";
    closeBtn.onclick = () => {
      overlay.classList.remove("open");
      if (uiState === "recording") {
        // Minimize only — recording continues, pill shows on screen
        overlayMinimized = true;
      } else if (uiState === "done" || uiState === "error") {
        resetState();
      }
    };
    overlay.appendChild(closeBtn);

    switch (uiState) {
      case "idle":
      case "ready":
        renderReady();
        break;
      case "recording":
        renderRecording();
        break;
      case "uploading":
        renderUploading();
        break;
      case "done":
        renderDone();
        break;
      case "error":
        renderError();
        break;
    }
  }

  function renderReady() {
    const title = document.createElement("h2");
    title.className = "fb-title";
    title.textContent = "Record Feedback";
    overlay.appendChild(title);

    const notice = document.createElement("p");
    notice.className = "fb-desc";
    notice.textContent =
      "Thanks for using this feedback tool. If you clicked on this " +
      "accidentally, feel free to close this panel or refresh the page. " +
      "By submitting a recording here, it will be sent to the website " +
      "support team at becomedistinct.com, and changes will be made to " +
      "the website based on the feedback you provide.";
    overlay.appendChild(notice);

    if (desktopMode) {
      // Desktop: warn about tab switching
      const warning = document.createElement("p");
      warning.className = "fb-desc fb-warning";
      warning.textContent =
        "Important: Once recording starts, please stay on this tab. " +
        "Switching tabs or minimizing the browser will break the screen capture. " +
        "Navigate within this page and talk through your feedback.";
      overlay.appendChild(warning);

      const desc = document.createElement("p");
      desc.className = "fb-desc";
      desc.textContent =
        "Your browser will ask permission to capture your screen and microphone.";
      overlay.appendChild(desc);
    } else {
      // Mobile: explain audio + screenshots mode
      const desc = document.createElement("p");
      desc.className = "fb-desc";
      desc.textContent =
        "On mobile, we'll record your voice and automatically capture " +
        "screenshots of the page as you scroll. Navigate the page and talk " +
        "through your feedback — we'll capture what you're looking at.";
      overlay.appendChild(desc);

      const desc2 = document.createElement("p");
      desc2.className = "fb-desc";
      desc2.textContent =
        "Your browser will ask permission to use your microphone.";
      overlay.appendChild(desc2);
    }

    const btn = document.createElement("button");
    btn.className = "fb-btn fb-btn-primary";
    btn.textContent = "Start Recording";
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "Requesting permissions...";
      if (desktopMode) {
        await startRecording();
      } else {
        await startMobileRecording();
      }
    };
    overlay.appendChild(btn);
  }

  function renderRecording() {
    const title = document.createElement("h2");
    title.className = "fb-title";
    title.textContent = "Recording...";
    overlay.appendChild(title);

    const indicator = document.createElement("div");
    indicator.className = "fb-recording-indicator";
    const dot = document.createElement("div");
    dot.className = "fb-dot";
    const timer = document.createElement("span");
    timer.className = "fb-timer";
    timer.textContent = formatTime(durationSeconds);
    indicator.appendChild(dot);
    indicator.appendChild(timer);
    overlay.appendChild(indicator);

    if (!desktopMode) {
      // Mobile: show screenshot count
      const ssInfo = document.createElement("p");
      ssInfo.className = "fb-desc";
      ssInfo.textContent = `${screenshotCount} screenshot${screenshotCount !== 1 ? "s" : ""} captured — scroll the page to capture different areas.`;
      overlay.appendChild(ssInfo);
    }

    const desc = document.createElement("p");
    desc.className = "fb-desc";
    desc.textContent = desktopMode
      ? "Stay on this tab — navigate the page and talk through your feedback. Click stop when you're done."
      : "Talk through your feedback and scroll to show different parts of the page. Click stop when you're done.";
    overlay.appendChild(desc);

    const btnRow = document.createElement("div");
    btnRow.className = "fb-btn-row";

    const stopBtn = document.createElement("button");
    stopBtn.className = "fb-btn fb-btn-danger";
    stopBtn.textContent = "Stop & Submit";
    stopBtn.onclick = () => stopRecording();
    btnRow.appendChild(stopBtn);

    const minBtn = document.createElement("button");
    minBtn.className = "fb-btn fb-btn-secondary";
    minBtn.textContent = "Minimize";
    minBtn.onclick = () => {
      overlayMinimized = true;
      overlay.classList.remove("open");
    };
    btnRow.appendChild(minBtn);

    overlay.appendChild(btnRow);
  }

  function renderUploading() {
    const title = document.createElement("h2");
    title.className = "fb-title";
    title.textContent = "Uploading...";
    overlay.appendChild(title);

    const wrap = document.createElement("div");
    wrap.className = "fb-progress-wrap";

    const bar = document.createElement("div");
    bar.className = "fb-progress-bar";
    const fill = document.createElement("div");
    fill.className = "fb-progress-fill";
    fill.style.width = `${uploadPercent}%`;
    bar.appendChild(fill);
    wrap.appendChild(bar);

    const text = document.createElement("div");
    text.className = "fb-progress-text";
    text.textContent = `${uploadPercent}%`;
    wrap.appendChild(text);

    overlay.appendChild(wrap);
  }

  function renderDone() {
    const wrap = document.createElement("div");
    wrap.className = "fb-success";

    const icon = document.createElement("div");
    icon.className = "fb-success-icon";
    icon.textContent = "\u2705";
    wrap.appendChild(icon);

    const title = document.createElement("h2");
    title.className = "fb-success-title";
    title.textContent = "Thank you!";
    wrap.appendChild(title);

    const desc = document.createElement("p");
    desc.className = "fb-success-desc";
    desc.textContent =
      "Your feedback has been submitted. We appreciate you taking the time to help us improve.";
    wrap.appendChild(desc);

    overlay.appendChild(wrap);
  }

  function renderError() {
    const title = document.createElement("h2");
    title.className = "fb-title";
    title.textContent = "Something went wrong";
    overlay.appendChild(title);

    const err = document.createElement("div");
    err.className = "fb-error";
    err.textContent = errorMessage;
    overlay.appendChild(err);

    const btn = document.createElement("button");
    btn.className = "fb-btn fb-btn-primary";
    btn.textContent = "Try Again";
    btn.onclick = () => {
      resetState();
      setUIState("ready");
    };
    overlay.appendChild(btn);
  }

  async function handleUpload() {
    if (!recordingBlob) return;
    setUIState("uploading");

    const onProgress = (percent: number) => {
      uploadPercent = percent;
      render();
    };

    try {
      if (isMobileMode()) {
        await uploadMobileRecording({
          blob: recordingBlob,
          screenshots: getScreenshots(),
          siteId: config.siteId,
          apiBase: config.apiBase,
          onProgress,
        });
      } else {
        await uploadRecording({
          blob: recordingBlob,
          siteId: config.siteId,
          apiBase: config.apiBase,
          onProgress,
        });
      }
      setUIState("done");
    } catch (err) {
      errorMessage =
        err instanceof Error ? err.message : "Upload failed";
      setUIState("error");
    }
  }

  function resetState() {
    recordingBlob = null;
    durationSeconds = 0;
    uploadPercent = 0;
    errorMessage = "";
    screenshotCount = 0;
    uiState = "idle";
  }

  // Trigger click: open overlay normally, or restore it if minimized during recording
  trigger.onclick = () => {
    if (uiState === "recording") {
      // Restore the panel so user can stop recording
      overlayMinimized = false;
      overlay.classList.add("open");
      render();
    } else {
      setUIState("ready");
      overlay.classList.add("open");
    }
  };

  // Initial render
  render();
}
