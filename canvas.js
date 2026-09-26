"use strict";
// Preserve the accepted image until normal damage painting takes over. Resizing
// the same canvas keeps pointer identity and never requests an extra full frame.
function floeResizeCanvas(canvas, width, height) {
  if (canvas.width === width && canvas.height === height) return;
  const saved = typeof document === "undefined" ? new OffscreenCanvas(canvas.width, canvas.height) : document.createElement("canvas");
  saved.width = canvas.width; saved.height = canvas.height;
  if (saved.width && saved.height) saved.getContext("2d").drawImage(canvas, 0, 0);
  canvas.width = width; canvas.height = height;
  if (saved.width && saved.height) canvas.getContext("2d").drawImage(saved, 0, 0);
}
