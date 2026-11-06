// After a photo is chosen, the uploader marks the item lines and their prices. OCR then reads only
// that area, so the total and other numbers on the receipt can't be mistaken for items.
// Drag out a box, then drag its corners to follow a tilted receipt, or drag inside it to move it.
(() => {
  const input = document.getElementById("receipt-image");
  const dialog = document.getElementById("focus-dialog");
  if (!input || !dialog) return;
  const form = input.form;
  const image = document.getElementById("focus-preview");
  const surface = image.parentElement;
  const shade = dialog.querySelector(".focus-shade");
  const handles = [...dialog.querySelectorAll(".focus-handle")];
  const tools = dialog.querySelector(".focus-tools");
  const detect = dialog.querySelector('[data-focus="detect"]');
  let quad = null;  // [x, y] corners normalized to the photo: top left, top right, bottom right, bottom left
  let drag = null;  // { corner } | { from } to move the area | { origin } to draw a new one; all keep `before`

  const clamp = (v) => Math.min(1, Math.max(0, v));
  const at = (event) => {
    const r = image.getBoundingClientRect();
    return [clamp((event.clientX - r.left) / r.width), clamp((event.clientY - r.top) / r.height)];
  };
  const bounds = (q) => {
    const xs = q.map((p) => p[0]), ys = q.map((p) => p[1]);
    const x = Math.min(...xs), y = Math.min(...ys);
    return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
  };
  const big = (q) => { const b = bounds(q); return b.w >= 0.05 && b.h >= 0.05; };  // A tap is not an area.
  function inside(q, [x, y]) {
    let hit = false;
    q.forEach(([x1, y1], i) => {
      const [x2, y2] = q[(i + 1) % q.length];
      if ((y1 > y) !== (y2 > y) && x < x1 + (y - y1) * (x2 - x1) / (y2 - y1)) hit = !hit;
    });
    return hit;
  }

  function paint(next) {
    quad = next;
    shade.toggleAttribute("hidden", !quad);  // An SVG element has no .hidden property.
    // The row keeps its space, so the dialog doesn't grow and shift the photo mid-drag.
    tools.querySelector("span").textContent = quad ? "Drag a corner to fit tilted lines." : "Then drag its corners to fit tilted lines.";
    tools.querySelector("button").style.visibility = quad ? "" : "hidden";
    detect.disabled = !quad;
    handles.forEach((handle, i) => {
      handle.hidden = !quad;
      if (quad) Object.assign(handle.style, { left: `${quad[i][0] * 100}%`, top: `${quad[i][1] * 100}%` });
    });
    if (!quad) return;
    const edge = `M${quad.map((p) => p.join(" ")).join("L")}Z`;
    shade.querySelector(".edge").setAttribute("d", edge);
    shade.querySelector(".shade").setAttribute("d", `M0 0H1V1H0Z${edge}`);  // Dims all but the area.
  }

  surface.addEventListener("pointerdown", (event) => {
    if (event.button > 0) return;
    event.preventDefault();
    surface.setPointerCapture(event.pointerId);
    const point = at(event);
    const handle = event.target.closest(".focus-handle");
    if (handle) drag = { corner: Number(handle.dataset.corner), before: quad };
    else if (quad && inside(quad, point)) drag = { from: point, before: quad };
    else drag = { origin: point, before: quad };
  });
  surface.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const [x, y] = at(event);
    if ("corner" in drag) {
      paint(quad.map((p, i) => (i === drag.corner ? [x, y] : p)));
    } else if (drag.from) {  // Moved whole, kept on the photo.
      const b = bounds(drag.before);
      const dx = Math.min(Math.max(x - drag.from[0], -b.x), 1 - b.x - b.w);
      const dy = Math.min(Math.max(y - drag.from[1], -b.y), 1 - b.y - b.h);
      paint(drag.before.map(([px, py]) => [px + dx, py + dy]));
    } else {
      const [ox, oy] = drag.origin;
      const x1 = Math.min(ox, x), x2 = Math.max(ox, x), y1 = Math.min(oy, y), y2 = Math.max(oy, y);
      paint([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]);
    }
  });
  const release = () => {
    // A tap, or an area squeezed too small to read, leaves the previous area as it was.
    if (drag && !(quad && big(quad))) paint(drag.before);
    drag = null;
  };
  surface.addEventListener("pointerup", release);
  surface.addEventListener("pointercancel", release);
  dialog.querySelector('[data-focus="reset"]').addEventListener("click", () => paint(null));

  input.addEventListener("change", () => {
    if (!input.files.length) return;
    URL.revokeObjectURL(image.src);
    image.src = URL.createObjectURL(input.files[0]);
    paint(null);
    dialog.returnValue = "";
    dialog.showModal();
  });

  function upload(withFocus) {
    const b = withFocus && bounds(quad);
    for (const key of ["x", "y", "w", "h", "quad"]) {
      const field = form.elements[`focus_${key}`];
      field.disabled = !withFocus;  // Disabled fields aren't sent: no focus means the whole photo.
      if (!withFocus) continue;
      field.value = key === "quad" ? JSON.stringify(quad.map((p) => p.map((v) => +v.toFixed(4)))) : b[key].toFixed(4);
    }
    dialog.close("upload");
    form.requestSubmit();
  }
  detect.addEventListener("click", () => upload(true));
  dialog.querySelector('[data-focus="whole"]').addEventListener("click", () => upload(false));
  // Cancelled: forget the file so picking the same photo again still opens this step.
  dialog.addEventListener("close", () => { if (dialog.returnValue !== "upload") input.value = ""; });
})();
