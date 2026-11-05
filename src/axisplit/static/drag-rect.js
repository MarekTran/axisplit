// Drag out a rectangle over an image, reported normalized 0..1 to the image: the same space as
// item boxes and the upload focus area. Only active while isActive() is true; the surface then
// needs touch-action: none (CSS) so the drag draws instead of scrolling the page.
function dragRect(surface, image, isActive, onRect) {
  let start = null;
  const at = (event) => {
    const r = image.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (event.clientY - r.top) / r.height)),
    };
  };
  const between = (a, b) => ({
    x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y),
  });
  surface.addEventListener("pointerdown", (event) => {
    if (!isActive() || event.button > 0) return;
    event.preventDefault();
    surface.setPointerCapture(event.pointerId);
    start = at(event);
    onRect(between(start, start), false);
  });
  surface.addEventListener("pointermove", (event) => {
    if (start) onRect(between(start, at(event)), false);
  });
  surface.addEventListener("pointerup", (event) => {
    if (!start) return;
    const rect = between(start, at(event));
    start = null;
    onRect(rect, true);
  });
  surface.addEventListener("pointercancel", () => { start = null; });
}

// Places an absolutely positioned element over the same normalized rect.
function placeRect(el, rect) {
  Object.assign(el.style, {
    left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%`,
  });
}
