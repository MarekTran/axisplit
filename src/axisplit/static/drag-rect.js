// A box on the photo that can be drawn and then adjusted: drag a corner handle to resize it, drag
// inside it to move it, drag elsewhere to draw a new one; a tap draws a line-sized box there.
// Normalized 0..1 to the image: the same space as item boxes. Only active while isActive() is true;
// the surface then needs touch-action: none (CSS) so drags edit instead of scrolling the page.
function boxEditor(surface, image, layer, isActive, onChange) {
  const el = Object.assign(document.createElement("div"), { className: "box draft", hidden: true });
  for (const corner of [0, 1, 2, 3]) {  // top left, top right, bottom right, bottom left
    const handle = Object.assign(document.createElement("span"), { className: "corner-handle" });
    handle.dataset.corner = corner;
    el.append(handle);
  }
  layer.append(el);
  let rect = null;
  let drag = null;  // { corner } to resize | { from } to move | { origin } to draw; all keep `before`

  const clamp = (v) => Math.min(1, Math.max(0, v));
  const at = (event) => {
    const r = image.getBoundingClientRect();
    return { x: clamp((event.clientX - r.left) / r.width), y: clamp((event.clientY - r.top) / r.height) };
  };
  const between = (a, b) => ({
    x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y),
  });
  const corners = (r) => [{ x: r.x, y: r.y }, { x: r.x + r.w, y: r.y }, { x: r.x + r.w, y: r.y + r.h }, { x: r.x, y: r.y + r.h }];
  const inside = (r, p) => p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h;

  function set(next) {
    rect = next;
    el.hidden = !rect;
    if (rect) placeRect(el, rect);
  }
  surface.addEventListener("pointerdown", (event) => {
    if (!isActive() || event.button > 0) return;
    event.preventDefault();
    surface.setPointerCapture(event.pointerId);
    const point = at(event);
    const handle = event.target.closest(".corner-handle");
    if (handle && rect) drag = { corner: Number(handle.dataset.corner), before: rect };
    else if (rect && inside(rect, point)) drag = { from: point, before: rect };
    else drag = { origin: point, before: rect };
  });
  surface.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const p = at(event);
    if ("corner" in drag) {
      set(between(corners(drag.before)[(drag.corner + 2) % 4], p));  // The opposite corner stays put.
    } else if (drag.from) {  // Moved whole, kept on the photo.
      const b = drag.before;
      const dx = Math.min(Math.max(p.x - drag.from.x, -b.x), 1 - b.x - b.w);
      const dy = Math.min(Math.max(p.y - drag.from.y, -b.y), 1 - b.y - b.h);
      set({ ...b, x: b.x + dx, y: b.y + dy });
    } else {
      set(between(drag.origin, p));
    }
    onChange(rect, false);
  });
  const release = () => {
    if (!drag) return;
    if (drag.origin && (!rect || rect === drag.before || (rect.w < 0.02 && rect.h < 0.02))) {
      // A tap: a line-sized box centered on it, kept on the photo.
      const { x, y } = drag.origin;
      set({ x: Math.min(Math.max(x - 0.3, 0), 0.4), y: Math.min(Math.max(y - 0.025, 0), 0.95), w: 0.6, h: 0.05 });
    } else if (drag.origin && rect.h < 0.02) {  // A swipe along the line: a line-high box.
      set({ ...rect, y: Math.min(Math.max(rect.y + rect.h / 2 - 0.02, 0), 0.96), h: 0.04 });
    } else if (rect.w < 0.02 || rect.h < 0.01) {
      set(drag.before);  // Squeezed to nothing: keep what was there.
    }
    drag = null;
    if (rect) onChange(rect, true);
  };
  surface.addEventListener("pointerup", release);
  surface.addEventListener("pointercancel", release);
  return { set, get: () => rect };
}

// Places an absolutely positioned element over the same normalized rect.
function placeRect(el, rect) {
  Object.assign(el.style, {
    left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%`,
  });
}
