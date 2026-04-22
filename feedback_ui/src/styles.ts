/**
 * Inlined CSS for the feedback widget.
 * All styles are scoped inside the Shadow DOM so they don't leak into the host page.
 */

export const WIDGET_CSS = `
  :host {
    all: initial;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    color: #1a1a1a;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  .fb-trigger {
    position: fixed;
    bottom: 4px;
    right: 4px;
    font-size: 9px;
    color: #bbb;
    opacity: 0.25;
    text-decoration: none;
    cursor: default;
    z-index: 2147483646;
    transition: opacity 0.3s, background 0.3s, color 0.3s, padding 0.3s, border-radius 0.3s;
    background: none;
    border: none;
    font-family: inherit;
    letter-spacing: 0.5px;
  }
  .fb-trigger:hover {
    opacity: 0.6;
    cursor: pointer;
  }
  .fb-trigger.fb-trigger-recording {
    bottom: 16px;
    right: 16px;
    font-size: 13px;
    font-weight: 600;
    color: #fff;
    background: #dc2626;
    opacity: 1;
    cursor: pointer;
    padding: 10px 18px;
    border-radius: 999px;
    letter-spacing: 0;
    animation: fb-pill-pulse 1.5s ease-in-out infinite;
    box-shadow: 0 2px 12px rgba(220,38,38,0.5);
  }
  @keyframes fb-pill-pulse {
    0%, 100% { box-shadow: 0 2px 12px rgba(220,38,38,0.5); }
    50% { box-shadow: 0 2px 20px rgba(220,38,38,0.85); }
  }

  .fb-overlay {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 340px;
    background: #fff;
    box-shadow: -4px 0 24px rgba(0,0,0,0.15);
    z-index: 2147483647;
    display: flex;
    flex-direction: column;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    padding: 24px;
  }
  .fb-overlay.open {
    transform: translateX(0);
  }

  .fb-close {
    position: absolute;
    top: 12px;
    right: 12px;
    background: none;
    border: none;
    font-size: 20px;
    cursor: pointer;
    color: #666;
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
  }
  .fb-close:hover {
    background: #f0f0f0;
  }

  .fb-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
    color: #111;
  }

  .fb-desc {
    font-size: 13px;
    color: #666;
    line-height: 1.5;
    margin-bottom: 24px;
  }

  .fb-warning {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 12px 14px;
    color: #92400e;
    font-weight: 500;
  }

  .fb-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
    font-family: inherit;
    width: 100%;
  }
  .fb-btn:active {
    transform: scale(0.98);
  }

  .fb-btn-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .fb-btn-secondary {
    background: #f3f4f6;
    color: #374151;
    border: 1px solid #d1d5db;
  }
  .fb-btn-secondary:hover {
    background: #e5e7eb;
  }

  .fb-btn-primary {
    background: #2563eb;
    color: #fff;
  }
  .fb-btn-primary:hover {
    background: #1d4ed8;
  }

  .fb-btn-danger {
    background: #dc2626;
    color: #fff;
  }
  .fb-btn-danger:hover {
    background: #b91c1c;
  }

  .fb-recording-indicator {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
    padding: 12px 16px;
    background: #fef2f2;
    border-radius: 8px;
    border: 1px solid #fecaca;
  }

  .fb-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #dc2626;
    animation: fb-pulse 1s ease-in-out infinite;
  }

  @keyframes fb-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .fb-timer {
    font-size: 16px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: #dc2626;
  }

  .fb-progress-wrap {
    margin-bottom: 16px;
  }

  .fb-progress-bar {
    height: 8px;
    background: #e5e7eb;
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 8px;
  }

  .fb-progress-fill {
    height: 100%;
    background: #2563eb;
    border-radius: 4px;
    transition: width 0.3s ease;
  }

  .fb-progress-text {
    font-size: 13px;
    color: #666;
    text-align: center;
  }

  .fb-success {
    text-align: center;
    padding: 32px 0;
  }

  .fb-success-icon {
    font-size: 48px;
    margin-bottom: 16px;
  }

  .fb-success-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
    color: #111;
  }

  .fb-success-desc {
    font-size: 13px;
    color: #666;
    line-height: 1.5;
  }

  .fb-error {
    padding: 12px 16px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 8px;
    color: #dc2626;
    font-size: 13px;
    margin-bottom: 16px;
  }
`;
