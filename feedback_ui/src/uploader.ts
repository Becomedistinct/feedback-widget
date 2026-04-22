/**
 * Uploads recorded feedback to the backend.
 * Desktop: single video blob. Mobile: audio blob + screenshot images.
 * Uses XMLHttpRequest for upload progress reporting.
 */

export interface UploadOptions {
  blob: Blob;
  siteId: string;
  apiBase: string;
  onProgress?: (percent: number) => void;
}

export interface MobileUploadOptions extends UploadOptions {
  screenshots: Blob[];
}

export interface UploadResult {
  submission_id: string;
  ticket_id: string | null;
  zoho_error: string | null;
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

export function uploadRecording(opts: UploadOptions): Promise<UploadResult> {
  const form = new FormData();
  form.append("video", opts.blob, "feedback.webm");
  form.append("site_id", opts.siteId);
  form.append("page_url", window.location.href);
  form.append("user_agent", navigator.userAgent);

  return doUpload(form, `${opts.apiBase}/api/feedback/submit`, opts.onProgress);
}

export function uploadMobileRecording(opts: MobileUploadOptions): Promise<UploadResult> {
  const form = new FormData();
  form.append("audio", opts.blob, "feedback-audio.webm");
  form.append("site_id", opts.siteId);
  form.append("page_url", window.location.href);
  form.append("user_agent", navigator.userAgent);

  // Attach each screenshot as a numbered file
  opts.screenshots.forEach((screenshot, i) => {
    form.append("screenshots", screenshot, `screenshot-${i}.jpg`);
  });

  return doUpload(form, `${opts.apiBase}/api/feedback/submit-mobile`, opts.onProgress);
}
