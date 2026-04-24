/**
 * Feedback widget — injects a hidden trigger link and a slide-in recording overlay
 * into the host page using Shadow DOM for style isolation.
 *
 * Desktop: screen + mic recording via getDisplayMedia.
 * Mobile: audio only — device/session context is sent at upload time.
 */

import { WIDGET_CSS } from "./styles";
import {
  startRecording,
  startMobileRecording,
  stopRecording,
  setStatusCallback,
  isMobileMode,
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

  // Create Shadow DOM host — try to place it in the page footer, fall back to body
  const host = document.createElement("div");
  host.id = "feedback-widget-host";
  const shadow = host.attachShadow({ mode: "open" });

  const footerEl = document.querySelector<HTMLElement>(
    'footer, #colophon, #footer, .site-footer, .footer, [role="contentinfo"]'
  );
  if (footerEl) {
    host.setAttribute("data-placement", "footer");
    footerEl.appendChild(host);
  } else {
    host.setAttribute("data-placement", "fixed");
    document.body.appendChild(host);
  }

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
  let submitterEmail = "";
  let overlayMinimized = false;

  // DOM references
  const trigger = document.createElement("button");
  trigger.className = "fb-trigger";
  trigger.textContent = "Feedback";
  shadow.appendChild(trigger);

  // Fixed-mode fallback: show trigger only when near the bottom (scroll depth ≥ 70%)
  if (!footerEl) {
    trigger.style.display = "none";
    function checkScroll() {
      const scrolled = window.scrollY + window.innerHeight;
      const total = document.documentElement.scrollHeight;
      trigger.style.display = total <= window.innerHeight + 100 || scrolled >= total * 0.7 ? "" : "none";
    }
    window.addEventListener("scroll", checkScroll, { passive: true });
    checkScroll();
  }

  const overlay = document.createElement("div");
  overlay.className = "fb-overlay";
  shadow.appendChild(overlay);

  // Recorder status callback
  setStatusCallback((state: RecorderState) => {
    if (state.status === "recording") {
      durationSeconds = state.durationSeconds;
      setUIState("recording");
    } else if (state.status === "stopped" && state.blob) {
      recordingBlob = state.blob;
      durationSeconds = state.durationSeconds;
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
      // Mobile: audio + device context
      const desc = document.createElement("p");
      desc.className = "fb-desc";
      desc.textContent =
        "We'll record your voice along with details about your device and " +
        "the page you're viewing. Talk through your feedback and we'll send " +
        "everything to the support team.";
      overlay.appendChild(desc);

      const desc2 = document.createElement("p");
      desc2.className = "fb-desc";
      desc2.textContent =
        "Your browser will ask permission to use your microphone.";
      overlay.appendChild(desc2);
    }

    const emailWrap = document.createElement("div");
    emailWrap.style.cssText = "margin-bottom:16px";

    const emailLabel = document.createElement("label");
    emailLabel.style.cssText = "display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:4px";
    emailLabel.textContent = "Your email (optional)";
    emailWrap.appendChild(emailLabel);

    const emailInput = document.createElement("input");
    emailInput.type = "email";
    emailInput.placeholder = "you@example.com";
    emailInput.value = submitterEmail;
    emailInput.style.cssText = "width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;font-family:inherit;box-sizing:border-box";
    emailInput.oninput = () => { submitterEmail = emailInput.value.trim(); };
    emailWrap.appendChild(emailInput);

    const emailHint = document.createElement("p");
    emailHint.style.cssText = "font-size:12px;color:#9ca3af;margin-top:4px";
    emailHint.textContent = "Website owner? Leave this blank — we already have your details. Anyone else who'd like a notification when this is addressed, enter your email here.";
    emailWrap.appendChild(emailHint);

    overlay.appendChild(emailWrap);

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
      const uploadFn = isMobileMode() ? uploadMobileRecording : uploadRecording;
      await uploadFn({
        blob: recordingBlob,
        siteId: config.siteId,
        apiBase: config.apiBase,
        onProgress,
        submitterEmail,
      });
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
    submitterEmail = "";
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
