/**
 * Screen + microphone recorder using browser MediaRecorder API.
 *
 * Desktop: captures current tab via getDisplayMedia + microphone, records to webm.
 * Mobile: captures microphone audio only. Device/session context is sent at upload time.
 */

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
let mobileMode = false;

export function isMobileMode(): boolean {
  return mobileMode;
}

function currentState(): RecorderState {
  return {
    status: getStatus(),
    error: null,
    blob: null,
    durationSeconds: startTime ? (Date.now() - startTime) / 1000 : 0,
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
    displayStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true,
      // @ts-expect-error preferCurrentTab + surfaceSwitching are newer APIs not in all TS type defs
      preferCurrentTab: true,
      surfaceSwitching: "exclude",
    });

    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

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

    displayStream.getVideoTracks()[0].onended = () => {
      if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
      }
    };

    mediaRecorder.start(1000);
    startTime = Date.now();

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
 * Mobile recording: microphone audio only.
 * Device/session context is collected and sent at upload time.
 */
export async function startMobileRecording(): Promise<void> {
  mobileMode = true;
  notify({ status: "requesting" });

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

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
      const blob = new Blob(chunks, { type: mimeType });
      const duration = startTime ? (Date.now() - startTime) / 1000 : 0;
      notify({ status: "stopped", blob, durationSeconds: duration });
      cleanup();
    };

    mediaRecorder.onerror = () => {
      notify({ status: "error", error: "Recording failed" });
      cleanup();
    };

    mediaRecorder.start(1000);
    startTime = Date.now();

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
  mediaRecorder = null;
  startTime = 0;
}
