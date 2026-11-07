// After a photo is chosen, the uploader marks the item lines and their prices. OCR then reads only
// that area, so the total and other numbers on the receipt can't be mistaken for items.
// Drag out a box, then drag its corners to follow a tilted receipt, or drag inside it to move it.
// Two fingers, the wheel or the buttons zoom; once the server finds the receipt's text, the view
// zooms to it. Rotate and Straighten turn the photo; the upload is turned the same way.
(() => {
  const input = document.getElementById("receipt-image");
  const dialog = document.getElementById("focus-dialog");
  if (!input || !dialog) return;
  const form = input.form;
  const image = document.getElementById("focus-preview");
  const viewport = dialog.querySelector(".focus-viewport");
  const surface = dialog.querySelector(".focus-surface");  // the photo as turned; corners are 0..1 of it
  const shade = dialog.querySelector(".focus-shade");
  const handles = [...dialog.querySelectorAll(".focus-handle")];
  const tools = dialog.querySelector(".focus-tools");
  const detect = dialog.querySelector('[data-focus="detect"]');
  const straighten = document.getElementById("focus-straighten");
  const zoomButtons = Object.fromEntries([...dialog.querySelectorAll("[data-zoom]")].map((b) => [b.dataset.zoom, b]));
  const MAX_ZOOM = 8;
  let quad = null;  // [x, y] corners normalized to the surface: top left, top right, bottom right, bottom left
  let drag = null;  // { corner } | { from } to move the area | { origin } to draw a new one; all keep `before`
  let turn = 0;  // quarter turns clockwise, besides the straighten angle
  let view = { z: 1, x: 0, y: 0 };  // the surface's zoom and offset in the viewport
  let fit = { w: 0, h: 0 };  // the surface's size at zoom 1
  const pointers = new Map();
  let pinch = null;
  let photo = 0;  // which chosen photo a zoom hint is for
  let moved = false;  // the uploader zoomed or drew: a late hint doesn't move the view under them

  const clamp = (v) => Math.min(1, Math.max(0, v));
  const at = (event) => {
    const r = surface.getBoundingClientRect();
    return [clamp((event.clientX - r.left) / r.width), clamp((event.clientY - r.top) / r.height)];
  };
  const local = (event) => {
    const r = viewport.getBoundingClientRect();
    return [event.clientX - r.left, event.clientY - r.top];
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

  // Turning: the surface is the turned photo's bounding box, as the server will store it.
  const angle = () => turn * 90 + Number(straighten.value);
  const radians = () => angle() * Math.PI / 180;
  function box(a) {  // in photo pixels
    const w = image.naturalWidth, h = image.naturalHeight, c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
    return { w: w * c + h * s, h: w * s + h * c };
  }
  function toPhoto([u, v], a) {  // surface 0..1 at angle a -> the photo as taken, 0..1
    const b = box(a), w = image.naturalWidth, h = image.naturalHeight;
    const x = u * b.w - b.w / 2, y = v * b.h - b.h / 2, c = Math.cos(a), s = Math.sin(a);
    return [(x * c + y * s) / w + 0.5, (y * c - x * s) / h + 0.5];
  }
  function fromPhoto([px, py], a) {
    const b = box(a), x = (px - 0.5) * image.naturalWidth, y = (py - 0.5) * image.naturalHeight;
    const c = Math.cos(a), s = Math.sin(a);
    return [(x * c - y * s) / b.w + 0.5, (x * s + y * c) / b.h + 0.5];
  }
  function layout() {  // fits the turned photo in the viewport at zoom 1
    const b = box(radians());
    const f = Math.min(viewport.clientWidth / b.w, viewport.clientHeight / b.h);
    fit = { w: b.w * f, h: b.h * f };
    Object.assign(surface.style, { width: `${fit.w}px`, height: `${fit.h}px` });
    Object.assign(image.style, {
      width: `${image.naturalWidth * f}px`, height: `${image.naturalHeight * f}px`,
      transform: `translate(-50%, -50%) rotate(${angle()}deg)`,
    });
  }

  // Zoom and pan, kept so the photo never leaves a gap it could fill.
  function show(next, animate = false) {
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const z = Math.min(Math.max(next.z, 1), MAX_ZOOM), w = fit.w * z, h = fit.h * z;
    view = {
      z,
      x: w <= vw ? (vw - w) / 2 : Math.min(0, Math.max(vw - w, next.x)),
      y: h <= vh ? (vh - h) / 2 : Math.min(0, Math.max(vh - h, next.y)),
    };
    surface.classList.toggle("animate", animate);
    surface.style.transform = `translate(${view.x}px, ${view.y}px) scale(${z})`;
    surface.style.setProperty("--z", z);  // handles stay the same size on screen
    zoomButtons.out.disabled = zoomButtons.fit.disabled = z <= 1;
    zoomButtons.in.disabled = z >= MAX_ZOOM;
  }
  function zoomAt(factor, [vx, vy], animate = false) {  // keeps the viewport point (vx, vy) still
    const z = Math.min(Math.max(view.z * factor, 1), MAX_ZOOM), k = z / view.z;
    show({ z, x: vx - (vx - view.x) * k, y: vy - (vy - view.y) * k }, animate);
  }
  function zoomTo(r) {  // r: a rect on the surface, 0..1
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const z = Math.min(Math.max(Math.min(vw / (r.w * fit.w), vh / (r.h * fit.h)), 1), MAX_ZOOM);
    show({ z, x: vw / 2 - (r.x + r.w / 2) * fit.w * z, y: vh / 2 - (r.y + r.h / 2) * fit.h * z }, true);
  }
  const centre = () => [viewport.clientWidth / 2, viewport.clientHeight / 2];
  zoomButtons.in.addEventListener("click", () => { moved = true; zoomAt(1.6, centre(), true); });
  zoomButtons.out.addEventListener("click", () => zoomAt(1 / 1.6, centre(), true));
  zoomButtons.fit.addEventListener("click", () => show({ z: 1 }, true));
  viewport.addEventListener("wheel", (event) => {
    event.preventDefault();
    moved = true;
    zoomAt(Math.exp(-event.deltaY * (event.deltaMode ? 0.05 : 0.002)), local(event));
  }, { passive: false });

  // Turning keeps the area on the same part of the photo, and the view on the same spot.
  function turnTo(update) {
    const before = radians(), [cx, cy] = centre();
    const middle = toPhoto([(cx - view.x) / (fit.w * view.z), (cy - view.y) / (fit.h * view.z)], before);
    const corners = quad && quad.map((p) => toPhoto(p, before));
    update();
    layout();
    const after = radians();
    if (corners) paint(corners.map((p) => fromPhoto(p, after).map(clamp)));
    const [u, v] = fromPhoto(middle, after);
    show({ z: view.z, x: cx - u * fit.w * view.z, y: cy - v * fit.h * view.z });
  }
  dialog.querySelector('[data-focus="turn"]').addEventListener("click", () => turnTo(() => { turn = (turn + 1) % 4; }));
  let leveling;
  straighten.addEventListener("input", () => {
    turnTo(() => {});
    const value = Number(straighten.value);
    straighten.nextElementSibling.textContent = `${value > 0 ? "+" : ""}${value}°`;
    viewport.classList.add("leveling");  // lines to set the text against while it moves
    clearTimeout(leveling);
    leveling = setTimeout(() => viewport.classList.remove("leveling"), 1200);
  });

  function paint(next) {
    quad = next;
    shade.toggleAttribute("hidden", !quad);  // An SVG element has no .hidden property.
    // The row keeps its space, so the dialog doesn't grow and shift the photo mid-drag.
    tools.querySelector("span").textContent = quad ? "Drag a corner to fit tilted lines." : "Pinch to zoom. Then drag its corners to fit tilted lines.";
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

  // One finger draws and adjusts the area; a second finger turns it into a pinch that zooms and pans.
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button > 0 || event.target.closest("button")) return;
    event.preventDefault();
    try { viewport.setPointerCapture(event.pointerId); } catch { /* a pointer already gone */ }
    pointers.set(event.pointerId, local(event));
    if (pointers.size === 2) {
      if (drag) { paint(drag.before); drag = null; }  // the first finger was the start of a pinch
      pinch = { start: [...pointers.values()], view };
      moved = true;
    }
    if (pointers.size > 1 || pinch) return;
    moved = true;
    const point = at(event);
    const handle = event.target.closest(".focus-handle");
    if (handle) drag = { corner: Number(handle.dataset.corner), before: quad };
    else if (quad && inside(quad, point)) drag = { from: point, before: quad };
    else drag = { origin: point, before: quad };
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, local(event));
    if (pinch) {
      if (pointers.size < 2) return;
      const [a, b] = pinch.start, [c, d] = pointers.values();
      const z = Math.min(Math.max(pinch.view.z * Math.hypot(c[0] - d[0], c[1] - d[1]) / Math.hypot(a[0] - b[0], a[1] - b[1]), 1), MAX_ZOOM);
      const k = z / pinch.view.z, from = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], to = [(c[0] + d[0]) / 2, (c[1] + d[1]) / 2];
      show({ z, x: to[0] - (from[0] - pinch.view.x) * k, y: to[1] - (from[1] - pinch.view.y) * k });
      return;
    }
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
  const release = (event) => {
    if (!pointers.delete(event.pointerId)) return;
    if (pinch) { if (!pointers.size) pinch = null; return; }  // lifting the last finger ends the pinch
    // A tap, or an area squeezed too small to read, leaves the previous area as it was.
    if (drag && !(quad && big(quad))) paint(drag.before);
    drag = null;
  };
  viewport.addEventListener("pointerup", release);
  viewport.addEventListener("pointercancel", release);
  dialog.querySelector('[data-focus="reset"]').addEventListener("click", () => paint(null));

  // A small copy goes to the server, which says where the receipt's text is: the view zooms to it.
  async function zoomToReceipt(id) {
    try {
      const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = Object.assign(document.createElement("canvas"), {
        width: Math.round(image.naturalWidth * scale), height: Math.round(image.naturalHeight * scale),
      });
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      const body = new FormData();
      body.append("image", await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.85)), "hint.jpg");
      const response = await fetch(form.action.replace(/\/receipts$/, "/focus-hint"), { method: "POST", body });
      const { extent } = await response.json();
      if (id !== photo || moved || !extent || !dialog.open) return;
      const [x, y, w, h] = extent, a = radians();
      const r = bounds([[x, y], [x + w, y], [x + w, y + h], [x, y + h]].map((p) => fromPhoto(p, a)));
      const m = 0.02;  // a little paper around the text
      zoomTo({ x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m });
    } catch {
      // No hint: the whole photo stays in view, and zooming by hand still works.
    }
  }

  input.addEventListener("change", async () => {
    if (!input.files.length) return;
    URL.revokeObjectURL(image.src);
    image.src = URL.createObjectURL(input.files[0]);
    const id = ++photo;
    turn = 0;
    straighten.value = 0;
    straighten.nextElementSibling.textContent = "0°";
    moved = false;
    paint(null);
    dialog.returnValue = "";
    dialog.showModal();
    await image.decode().catch(() => {});
    if (id !== photo) return;
    layout();
    show({ z: 1 });
    zoomToReceipt(id);
  });
  window.addEventListener("resize", () => { if (dialog.open) { layout(); show(view); } });

  function upload(withFocus) {
    const b = withFocus && bounds(quad);
    for (const key of ["x", "y", "w", "h", "quad"]) {
      const field = form.elements[`focus_${key}`];
      field.disabled = !withFocus;  // Disabled fields aren't sent: no focus means the whole photo.
      if (!withFocus) continue;
      field.value = key === "quad" ? JSON.stringify(quad.map((p) => p.map((v) => +v.toFixed(4)))) : b[key].toFixed(4);
    }
    form.elements.rotation.value = angle();  // the stored photo is turned like the surface, so the area fits it
    dialog.close("upload");
    form.requestSubmit();
  }
  detect.addEventListener("click", () => upload(true));
  dialog.querySelector('[data-focus="whole"]').addEventListener("click", () => upload(false));
  // Cancelled: forget the file so picking the same photo again still opens this step.
  dialog.addEventListener("close", () => { if (dialog.returnValue !== "upload") input.value = ""; });
})();
