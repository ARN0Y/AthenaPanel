// Clipboard that also works on plain HTTP (navigator.clipboard needs a secure
// context, and the panel is still reachable over http://<ip>/<panel-path>/).
// Fallback selects a document Range — NOT element focus — so a dropdown's
// focus-trap can't blank the selection (which made execCommand report success
// while copying nothing).
export function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    try {
      const span = document.createElement("span");
      span.textContent = text;
      span.style.position = "fixed";
      span.style.left = "-9999px";
      span.style.top = "0";
      span.style.whiteSpace = "pre";
      document.body.appendChild(span);
      const sel = window.getSelection();
      const saved = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
      sel?.removeAllRanges();
      const range = document.createRange();
      range.selectNodeContents(span);
      sel?.addRange(range);
      const ok = document.execCommand("copy");
      sel?.removeAllRanges();
      if (saved) sel?.addRange(saved);
      document.body.removeChild(span);
      ok ? resolve() : reject(new Error("copy failed"));
    } catch (err) {
      reject(err);
    }
  });
}
