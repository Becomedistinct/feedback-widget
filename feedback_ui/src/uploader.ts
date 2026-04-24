/**
 * Uploads recorded feedback to the backend.
 * Desktop: video blob + device context. Mobile: audio blob + device context.
 * Uses XMLHttpRequest for upload progress reporting.
 */

export interface UploadOptions {
  blob: Blob;
  siteId: string;
  apiBase: string;
  onProgress?: (percent: number) => void;
  submitterEmail?: string;
}

export interface UploadResult {
  submission_id: string;
  ticket_id: string | null;
  zoho_error: string | null;
}

async function collectDeviceContext(): Promise<Record<string, string>> {
  const ctx: Record<string, string> = {
    page_title:    document.title,
    scroll_pos:    `${Math.round(window.scrollX)},${Math.round(window.scrollY)}`,
    screen_size:   `${screen.width}x${screen.height}`,
    viewport_size: `${window.innerWidth}x${window.innerHeight}`,
    pixel_ratio:   String(window.devicePixelRatio ?? 1),
    language:      navigator.language,
    timezone:      Intl.DateTimeFormat().resolvedOptions().timeZone,
    network_type:  (navigator as any).connection?.effectiveType ?? "",
  };
  try {
    const ua = await (navigator as any).userAgentData
      ?.getHighEntropyValues(["model", "platform", "platformVersion"]);
    if (ua?.model)           ctx.device_model = ua.model;
    if (ua?.platform)        ctx.os_platform  = ua.platform;
    if (ua?.platformVersion) ctx.os_version   = ua.platformVersion;
  } catch { /* Safari/Firefox — UA string already sent */ }
  return ctx;
}

function doUpload(form: FormData, url: string, onProgress?: (percent: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.statusText}`));
      }
    };

    xhr.onerror = () => reject(new Error("Upload failed: network error"));

    xhr.open("POST", url);
    xhr.send(form);
  });
}

export async function uploadRecording(opts: UploadOptions): Promise<UploadResult> {
  const form = new FormData();
  form.append("video", opts.blob, "feedback.webm");
  form.append("site_id", opts.siteId);
  form.append("page_url", window.location.href);
  form.append("user_agent", navigator.userAgent);
  if (opts.submitterEmail) form.append("submitter_email", opts.submitterEmail);

  const ctx = await collectDeviceContext();
  for (const [k, v] of Object.entries(ctx)) form.append(k, v);

  return doUpload(form, `${opts.apiBase}/api/feedback/submit`, opts.onProgress);
}

export async function uploadMobileRecording(opts: UploadOptions): Promise<UploadResult> {
  const form = new FormData();
  form.append("audio", opts.blob, "feedback-audio.webm");
  form.append("site_id", opts.siteId);
  form.append("page_url", window.location.href);
  form.append("user_agent", navigator.userAgent);
  if (opts.submitterEmail) form.append("submitter_email", opts.submitterEmail);

  const ctx = await collectDeviceContext();
  for (const [k, v] of Object.entries(ctx)) form.append(k, v);

  return doUpload(form, `${opts.apiBase}/api/feedback/submit-mobile`, opts.onProgress);
}
