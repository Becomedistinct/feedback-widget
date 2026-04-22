/**
 * Screen + microphone recorder using browser MediaRecorder API.
 *
 * Desktop: captures current tab via getDisplayMedia + microphone, records to webm.
 * Mobile: captures microphone audio only + periodic page screenshots via html2canvas.
 */
import html2canvas from "html2canvas";

export type RecorderStatus =
  | "idle"
  | "requesting"
  | "recording"
  | "stopped"
  | "error";

export interface RecorderState {
  status: RecorderStatus;
  error: string | null;
  blob: Blob | null;
  durationSeconds: number;
  screenshotCount?: number;
}

type StatusCallback = (state: RecorderState) => void;

let mediaRecorder: MediaRecorder | null = null;
let displayStream: MediaStream | null = null;
let micStream: MediaStream | null = null;
let audioContext: AudioContext | null = null;
let chunks: Blob[] = [];
let startTime = 0;
let timerInterval: ReturnType<typeof setInterval> | null = null;
let onStatusChange: StatusCallback | null = null;

// Mobile screenshot state
let screenshots: Blob[] = [];
let screenshotInterval: ReturnType<typeof setInterval> | null = null;
let mobileMode = false;
let capturingScreenshot = false; // prevent overlapping captures

export function isMobileMode(): boolean {
  return mobileMode;
}

export function getScreenshots(): Blob[] {
  return screenshots;
}

function currentState(): RecorderState {
  return {
    status: getStatus(),
    error: null,
    blob: null,
    durationSeconds: startTime ? (Date.now() - startTime) / 1000 : 0,
    screenshotCount: screenshots.length,
  };
}

function getStatus(): RecorderStatus {
  if (!mediaRecorder) return "idle";
  if (mediaRecorder.state === "recording") return "recording";
  if (mediaRecorder.state === "inactive" && chunks.length > 0) return "stopped";
  return "idle";
}

function notify(override?: Partial<RecorderState>) {
  if (onStatusChange) {
    onStatusChange({ ...currentState(), ...override });
  }
}

function pickMimeType(): string {
  const candidates = [
    "video/webm;codecs=vp9,opus",
    "video/webm;codecs=vp8,opus",
    "video/webm",
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "video/webm";
}

function pickAudioMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "audio/webm";
}

function mergeAudioStreams(
  displayAudioTracks: MediaStreamTrack[],
  micAudioTracks: MediaStreamTrack[]
): { stream: MediaStream; ctx: AudioContext } {
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();

  for (const track of displayAudioTracks) {
    const source = ctx.createMediaStreamSource(new MediaStream([track]));
    source.connect(dest);
  }
  for (const track of micAudioTracks) {
    const source = ctx.createMediaStreamSource(new MediaStream([track]));
    source.connect(dest);
  }

  return { stream: dest.stream, ctx };
}

async function captureScreenshot(): Promise<void> {
  // Skip if a capture is already in progress — html2canvas can be slow on mobile
  if (capturingScreenshot) return;
  capturingScreenshot = true;
  try {
    // html2canvas cannot render Shadow DOM, so the widget panel will never
    // appear in screenshots — no need to hide/show it (that was causing flicker).
    const canvas = await html2canvas(document.body, {
      scale: 0.75,         // smaller = faster on mobile, still readable
      useCORS: true,
      logging: false,
      allowTaint: true,    // don't abort on tainted images
    });

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.75)
    );
    if (blob) {
      screenshots.push(blob);
      notify(); // updates screenshot count in the UI
    }
  } catch (_e) {
    // Silently continue — missing a screenshot isn't fatal
  } finally {
    capturingScreenshot = false;
  }
}

export function setStatusCallback(cb: StatusCallback) {
  onStatusChange = cb;
}

/**
 * Desktop recording: screen + microphone via getDisplayMedia.
 */
export async function startRecording(): Promise<void> {
  mobileMode = false;
  notify({ status: "requesting" });

  try {
    // Request screen capture — preferCurrentTab hints the browser to auto-select current tab
    // surfaceSwitching: "exclude" hides the "share this tab instead" button during recording
    displayStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true,
      // @ts-expect-error preferCurrentTab + surfaceSwitching are newer APIs not in all TS type defs
      preferCurrentTab: true,
      surfaceSwitching: "exclude",
    });

    // Request microphone
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

    // Merge audio: display audio (if any) + mic
    const displayAudioTracks = displayStream.getAudioTracks();
    const micAudioTracks = micStream.getAudioTracks();

    let combinedStream: MediaStream;

    if (displayAudioTracks.length > 0 || micAudioTracks.length > 0) {
      const merged = mergeAudioStreams(displayAudioTracks, micAudioTracks);
      audioContext = merged.ctx;
      combinedStream = new MediaStream([
        ...displayStream.getVideoTracks(),
        ...merged.stream.getAudioTracks(),
      ]);
    } else {
      combinedStream = displayStream;
    }

    // Set up MediaRecorder
    const mimeType = pickMimeType();
    chunks = [];
    mediaRecorder = new MediaRecorder(combinedStream, { mimeType });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }
      const blob = new Blob(chunks, { type: mimeType });
      const duration = startTime ? (Date.now() - startTime) / 1000 : 0;
      notify({ status: "stopped", blob, durationSeconds: duration });
      cleanup();
    };

    mediaRecorder.onerror = () => {
      notify({ status: "error", error: "Recording failed" });
      cleanup();
    };

    // Handle user clicking browser's "Stop sharing" button
    displayStream.getVideoTracks()[0].onended = () => {
      if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
      }
    };

    // Start recording with 1-second chunks
    mediaRecorder.start(1000);
    startTime = Date.now();

    // Timer to notify UI of duration updates
    timerInterval = setInterval(() => {
      notify({ status: "recording" });
    }, 1000);

    notify({ status: "recording" });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Failed to start recording";
    notify({ status: "error", error: message });
    cleanup();
  }
}

/**
 * Mobile recording: microphone audio + periodic page screenshots.
 */
export async function startMobileRecording(): Promise<void> {
  mobileMode = true;
  screenshots = [];
  notify({ status: "requesting" });

  try {
    // Request microphone
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

    // Set up audio-only MediaRecorder
    const mimeType = pickAudioMimeType();
    chunks = [];
    mediaRecorder = new MediaRecorder(micStream!, { mimeType });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }
      if (screenshotInterval) {
        clearInterval(screenshotInterval);
        screenshotInterval = null;
      }
      const blob = new Blob(chunks, { type: mimeType });
      const duration = startTime ? (Date.now() - startTime) / 1000 : 0;
      notify({
        status: "stopped",
        blob,
        durationSeconds: duration,
        screenshotCount: screenshots.length,
      });
      cleanup();
    };

    mediaRecorder.onerror = () => {
      notify({ status: "error", error: "Recording failed" });
      cleanup();
    };

    // Start recording audio
    mediaRecorder.start(1000);
    startTime = Date.now();

    // Capture screenshots every 4 seconds (first one after 1s so recording is active)
    screenshotInterval = setInterval(() => {
      captureScreenshot();
    }, 4000);
    // Initial capture after a short delay so recording is confirmed active
    setTimeout(() => captureScreenshot(), 800);

    // Timer to notify UI of duration updates
    timerInterval = setInterval(() => {
      notify({ status: "recording" });
    }, 1000);

    notify({ status: "recording" });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Failed to start recording";
    notify({ status: "error", error: message });
    cleanup();
  }
}

export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
  }
}

function cleanup() {
  if (displayStream) {
    displayStream.getTracks().forEach((t) => t.stop());
    displayStream = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (screenshotInterval) {
    clearInterval(screenshotInterval);
    screenshotInterval = null;
  }
  mediaRecorder = null;
  startTime = 0;
}
