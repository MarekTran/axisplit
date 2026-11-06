// SSR board: HTTP acknowledges edits; WebSocket broadcasts live peer changes.
// The review fragment is authoritative for amounts, confirmations and assignments.
(() => {
  const board = document.getElementById("board");
  if (!board) return;
  const rid = board.dataset.receipt;
  const who = board.dataset.who;
  const colors = JSON.parse(board.dataset.colors);
  const imgwrap = document.getElementById("imgwrap");
  const tokenbar = document.getElementById("tokenbar");
  let selected = colors[who] ? who : null;
  let ws;
  let closing = false;
  let reviewTimer;
  let feedbackTimer;
  let undo;
  let saving = 0; // Edits in flight. The review is refreshed once they have all settled.
  let queue = Promise.resolve();

  // The toast's button undoes a token op, or runs another action (a function) under its own label.
  function feedback(message, undoAction = null, label = "Undo") {
    const toast = document.getElementById("board-feedback");
    toast.querySelector("span").textContent = message;
    toast.hidden = false;
    undo = undoAction;
    const button = document.getElementById("undo-token");
    button.hidden = !undoAction;
    button.textContent = label;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { toast.hidden = true; undo = null; }, 8000);
  }
  document.getElementById("undo-token").addEventListener("click", () => {
    const action = undo;
    undo = null;
    if (typeof action === "function") action();
    else if (action) send(action, false);
  });

  function paintSelection() {
    for (const token of tokenbar.querySelectorAll(".token")) {
      const active = token.dataset.user === selected;
      token.classList.toggle("selected", active);
      token.setAttribute("aria-pressed", String(active));
    }
    document.getElementById("selection-hint").textContent = selected
      ? `Tap what ${selected} had`
      : "Choose a person, then tap what they had.";
    board.style.setProperty("--sel-c", colors[selected] || ""); // Tints the "+" on every item row.
  }
  tokenbar.addEventListener("click", (event) => {
    const token = event.target.closest(".token");
    if (!token) return;
    selected = token.dataset.user; // Selection is a choice, not an on/off switch.
    paintSelection();
  });
  paintSelection();

  function resolveItem(clientX, clientY) {
    const image = imgwrap.querySelector("img").getBoundingClientRect();
    const items = [...imgwrap.querySelectorAll(".box[data-item]")].map((el) => ({  // not the draft
      id: el.dataset.item, el,
      x: parseFloat(el.style.left) / 100 * image.width,
      y: parseFloat(el.style.top) / 100 * image.height,
      w: parseFloat(el.style.width) / 100 * image.width,
      h: parseFloat(el.style.height) / 100 * image.height,
    }));
    return closestReceiptItem(items, clientX - image.left, clientY - image.top);
  }
  function highlight(box) {
    box.classList.add("hit");
    setTimeout(() => box.classList.remove("hit"), 1500);
  }
  // spot: where along the item's box the token goes (0..1), when it was tapped on the photo.
  function place(itemId, spot = null) {
    if (!who) { feedback("Choose your name before placing tokens."); return; }
    if (!selected) { feedback("Choose a person above first."); return; }
    send({ op: "place", item_id: itemId, username: selected, ...(spot === null ? {} : { spot }) });
  }
  imgwrap.addEventListener("click", (event) => {
    if (drawing || Date.now() - drewAt < 500) return;  // That press drew a box.
    // A token sits where people tap, so a tap on someone else's token adds the selected person
    // beside it; only the selected person's own token comes off (anyone's comes off in the list).
    const pip = event.target.closest("button.pip");
    if (pip && (!selected || pip.dataset.user === selected)) {
      send({ op: "unplace", item_id: pip.closest(".box").dataset.item, username: pip.dataset.user });
      return;
    }
    const target = resolveItem(event.clientX, event.clientY);
    if (!target) { feedback("Detect the receipt first, or add a missing item below."); return; }
    if (who && selected) highlight(target.el);
    const box = target.el.getBoundingClientRect();  // The token sits where the finger was.
    place(target.id, Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)));
  });

  // Delegation survives SSR review-fragment replacements.
  board.addEventListener("click", (event) => {
    // List rows mirror the photo: a row adds the selected person, a holder chip takes theirs off.
    const add = event.target.closest("[data-add]");
    if (add) place(add.dataset.add);
    const holder = event.target.closest(".holder");
    if (holder) send({ op: "unplace", item_id: holder.dataset.item, username: holder.dataset.user });
    const edit = event.target.closest("[data-edit]");
    if (edit) openItem(edit);
    const confirm = event.target.closest("[data-confirm]");
    if (confirm && !confirm.disabled) send({ op: confirm.dataset.confirm });
    const jump = event.target.closest("[data-jump]");
    if (jump) {
      const box = imgwrap.querySelector(`.box[data-item="${jump.dataset.jump}"]`);
      if (box) {
        box.scrollIntoView({ block: "center", behavior: "smooth" });
        highlight(box);
        const label = itemLabel(jump.dataset.jump);
        feedback(`${label}: box in the wrong spot?`, () => startDrawing({ itemId: jump.dataset.jump, label }), "Fix box");
      }
    }
  });

  function itemLabel(id) {
    return board.querySelector(`.item-review li[data-item="${id}"] .item-add > strong`)?.textContent || "item";
  }
  // Edits queue up in order, so tapping an item five times places five tokens.
  function send(op, allowUndo = true) {
    if (!who) { location.href = `/claim?next=${encodeURIComponent(location.pathname)}`; return; }
    let message = null;
    if (op.op === "place" || op.op === "unplace") {
      const counts = tokenCounts(op.item_id);
      const count = (counts.get(op.username) || 0) + (op.op === "place" ? 1 : -1);
      if (count < 0) return;
      counts.set(op.username, count);
      const spots = tokenSpots(op.item_id);
      if (op.spot !== undefined) spots.set(op.username, op.spot);
      renderTokens(op.item_id, counts, spots); // Show the new count right away; the server's review replaces it.
      const label = itemLabel(op.item_id);
      message = !count ? `${op.username} removed from ${label}. Saved.`
        : count === 1 && op.op === "place" ? `${op.username} added to ${label}. Saved.`
        : `${op.username} ×${count} on ${label}. Saved.`;
    }
    feedback("Saving…");
    saving++;
    queue = queue.then(() => save(op, message, allowUndo)).finally(() => { if (!--saving) refreshReview(); });
  }
  async function save(op, message, allowUndo) {
    try {
      const response = await fetch(`/r/${rid}/ops`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...op, actor: who }),
      });
      const event = await response.json();
      if (!response.ok) throw new Error(event.detail || "Couldn’t save. Please try again.");
      if (message) {
        feedback(message, allowUndo ? { ...op, op: op.op === "place" ? "unplace" : "place" } : null);
      } else {
        feedback(op.op === "confirm" ? "Your confirmation is saved. Others can now check their shares." : "Your confirmation was withdrawn.");
      }
    } catch (error) {
      feedback(error.message || "Connection lost. Your change wasn’t confirmed; please retry.");
    }
  }

  // Fetch the authoritative state after edits and reconnects, including missed events.
  let reviewVersion = 0;
  async function refreshReview() {
    const version = ++reviewVersion;
    try {
      const response = await fetch(`/r/${rid}/review`);
      if (response.status === 410) { location.href = `/t/${board.dataset.tab}?removed=${rid}`; return; }
      if (!response.ok) throw new Error("Couldn’t refresh shares. Reload to check the latest split.");
      const html = await response.text();
      if (version !== reviewVersion || saving) return; // Newer taps are showing; refresh again after them.
      const next = new DOMParser().parseFromString(html, "text/html").getElementById("review");
      // Preserve keyboard focus when a control is replaced by server-rendered HTML.
      const active = document.activeElement;
      const focus = active?.closest("#review") ? {
        item: active.dataset.item, user: active.dataset.user, confirm: active.dataset.confirm, add: active.dataset.add,
      } : null;
      document.getElementById("review").replaceWith(next);
      if (focus) {
        const replacement = [...next.querySelectorAll("button")].find(button => focus.confirm ? button.dataset.confirm
          : focus.add ? button.dataset.add === focus.add
          : button.dataset.item === focus.item && button.dataset.user === focus.user);
        replacement?.focus({ preventScroll: true });
      }
      const status = document.getElementById("status");
      status.textContent = next.dataset.statusLabel;
      status.className = `badge ${next.dataset.status}`;
      syncPhoto();
      syncBoxTokens();
    } catch (error) { feedback(error.message); }
  }
  // Same order and markup as review.html and receipt.html: sorted by name, count shown from 2 up.
  function tokenCounts(itemId) {
    return new Map([...board.querySelectorAll(`.holder[data-item="${itemId}"]`)]
      .map(holder => [holder.dataset.user, Number(holder.dataset.count)]));
  }
  function tokenSpots(itemId) {  // null: no spot, the token sits at the box's right end
    return new Map([...board.querySelectorAll(`.holder[data-item="${itemId}"]`)]
      .map(holder => [holder.dataset.user, holder.dataset.spot === "" ? null : Number(holder.dataset.spot)]));
  }
  function byName([a], [b]) {
    const x = a.toLowerCase(), y = b.toLowerCase();
    return x < y ? -1 : x > y ? 1 : 0;
  }
  function tokenButton(className, user, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.dataset.user = user;
    button.style.setProperty("--c", colors[user] || "#888");
    button.setAttribute("aria-label", `Remove one of ${user}’s tokens from ${label}`);
    button.append(Object.assign(document.createElement("span"), { textContent: user.slice(0, 1) }));
    return button;
  }
  function renderPips(itemId, counts, spots) {
    const box = imgwrap.querySelector(`.box[data-item="${itemId}"]`);
    if (!box) return;
    const label = itemLabel(itemId);
    const placed = [...counts].filter(([, n]) => n > 0).sort(byName);
    box.querySelector(".tokens").replaceChildren(...placed.map(([user, n]) => {
      const pip = tokenButton("pip", user, label);
      pip.title = `Remove one of ${user}’s tokens`;
      if (n > 1) pip.append(Object.assign(document.createElement("b"), { textContent: n }));
      return pip;
    }));
    layoutPips(box, spots);
    box.classList.toggle("assigned", placed.length > 0);
  }
  // Each pip sits where its person tapped (no spot: the right end); pips that would overlap are
  // pushed apart along the box, so every person's token stays visible and tappable.
  function layoutPips(box, spots) {
    const width = box.clientWidth, half = 12, step = 26;
    const pips = [...box.querySelectorAll(".pip")].map((pip) => {
      const spot = spots.get(pip.dataset.user);
      return { pip, at: Math.min(Math.max(spot == null ? width : spot * width, half), width - half) };
    }).sort((a, b) => a.at - b.at);
    pips.forEach((p, i) => { if (i) p.at = Math.max(p.at, pips[i - 1].at + step); });
    for (let i = pips.length - 1; i >= 0; i--) {  // Pushed past the right end: back from there.
      pips[i].at = Math.min(pips[i].at, i === pips.length - 1 ? width - half : pips[i + 1].at - step);
    }
    for (const { pip, at } of pips) pip.style.left = `${(at / width) * 100}%`;
  }
  function renderTokens(itemId, counts, spots) {
    renderPips(itemId, counts, spots);
    const row = board.querySelector(`.item-review li[data-item="${itemId}"]`);
    const label = itemLabel(itemId);
    const placed = [...counts].filter(([, n]) => n > 0).sort(byName);
    const chips = placed.map(([user, n]) => {
      const chip = tokenButton("holder", user, label);
      Object.assign(chip.dataset, { item: itemId, count: n, spot: spots.get(user) ?? "" });
      Object.assign(chip.firstChild, { className: "avatar" }).setAttribute("aria-hidden", "true");
      chip.append(user);
      if (n > 1) chip.append(Object.assign(document.createElement("b"), { className: "count", textContent: `×${n}` }));
      return chip;
    });
    row.querySelector(".holders").replaceChildren(...(chips.length ? chips
      : [Object.assign(document.createElement("small"), { className: "unassigned-label", textContent: "Nobody yet" })]));
    row.classList.toggle("assigned", placed.length > 0);
    row.classList.toggle("unassigned", !placed.length);
  }
  function syncBoxTokens() {
    for (const box of imgwrap.querySelectorAll(".box")) {
      renderPips(box.dataset.item, tokenCounts(box.dataset.item), tokenSpots(box.dataset.item));
    }
  }
  syncBoxTokens();  // Spreads out pips the server placed on top of each other.
  // The photo follows the item list: deleted items' boxes go, restored or added ones appear,
  // moved ones move, and box numbers match the rows again.
  function syncPhoto() {
    const layer = imgwrap.querySelector(".photo");
    const rows = [...board.querySelectorAll(".item-review li[data-item]")];
    const ids = new Set(rows.map((row) => row.dataset.item));
    for (const box of layer.querySelectorAll(".box[data-item]")) if (!ids.has(box.dataset.item)) box.remove();
    rows.forEach((row, i) => {
      const id = row.dataset.item;
      let box = layer.querySelector(`.box[data-item="${id}"]`);
      if (!box) {
        box = Object.assign(document.createElement("div"), { className: "box" });
        box.dataset.item = id;
        box.append(...["box-number", "lbl", "tokens"].map((className) => Object.assign(document.createElement("span"), { className })));
        layer.insertBefore(box, layer.querySelector(".box.draft"));  // under the box being drawn
      }
      box.querySelector(".box-number").textContent = i + 1;
      box.querySelector(".lbl").textContent = `${itemLabel(id)} — ${row.querySelector(".item-add .amount").textContent}`;
      if (!box.classList.contains("editing")) {
        const [x, y, w, h] = row.querySelector("[data-box]").dataset.box.split(" ").map(Number);
        placeRect(box, { x, y, w, h });
      }
    });
  }

  // An item's dialog fixes a misread name or price, moves its box, or deletes it (with Undo).
  const itemDialog = document.getElementById("item-dialog");
  const itemForm = itemDialog.querySelector("form");
  let editingItem = null;
  function openItem(button) {
    if (!who) { location.href = `/claim?next=${encodeURIComponent(location.pathname)}`; return; }
    editingItem = button.dataset.edit;
    const number = button.closest("li").querySelector(".item-jump").textContent;
    itemDialog.querySelector("h2").textContent = `Edit item ${number}`;
    itemForm.elements.label.value = itemLabel(editingItem);
    itemForm.elements.price.value = (Number(button.dataset.price) / 100).toFixed(2).replace(".", ",");
    itemDialog.showModal();
  }
  itemForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = new FormData(itemForm);
    itemDialog.close();
    changeItem(editingItem, "", body, (item) => `Saved ${item.label}.`);
  });
  itemDialog.addEventListener("click", (event) => {
    const action = event.target.closest("[data-item-action]")?.dataset.itemAction;
    if (!action) return;
    itemDialog.close();
    if (action === "delete") deleteItem(editingItem);
    if (action === "box") {
      startDrawing({ itemId: editingItem, label: itemLabel(editingItem) });
      imgwrap.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  });
  function deleteItem(id) {
    changeItem(id, "/delete", null, (item) => `Deleted ${item.label}.`,
      () => changeItem(id, "/restore", null, (item) => `${item.label} is back.`));
  }
  // Queued with the token edits, so a delete and the taps around it stay in order.
  function changeItem(id, action, body, message, undoAction = null) {
    feedback("Saving…");
    saving++;
    queue = queue.then(async () => {
      try {
        const response = await fetch(`/r/${rid}/items/${id}${action}`, { method: "POST", body });
        const item = await response.json();
        if (!response.ok) throw new Error(item.detail || "Couldn’t save. Please try again.");
        feedback(message(item), undoAction);
      } catch (error) {
        feedback(error.message || "Connection lost. Your change wasn’t confirmed; please retry.");
      }
    }).finally(() => { if (!--saving) refreshReview(); });
  }
  // Just added from "Add a missing item": show where it went, and offer to take it back.
  const added = new URLSearchParams(location.search).get("added");
  if (added) {
    history.replaceState(null, "", location.pathname);
    const box = imgwrap.querySelector(`.box[data-item="${added}"]`);
    // Once the photo has its height, else the box isn't where it will be.
    if (box) imgwrap.querySelector("img").decode().catch(() => {}).then(() => { box.scrollIntoView({ block: "center" }); highlight(box); });
    feedback(`Added ${itemLabel(added)}. Edit it from the list.`, () => deleteItem(added));
  }
  // Drawing a box on the photo: for a missing item (while "Add a missing item" is open), or to
  // move an item's box where it is really printed. Either way the box can be adjusted (corners,
  // moving it) until it is saved; the bar under the photo says how and holds Save and Cancel.
  let drawing = null;  // { itemId, label } when redrawing, {} when adding a missing item
  let drewAt = 0;
  const manual = document.getElementById("manual-entry");
  const bar = document.getElementById("box-editor");
  const editor = boxEditor(imgwrap, imgwrap.querySelector("img"), imgwrap.querySelector(".photo"), () => drawing, (rect, done) => {
    if (done) drewAt = Date.now();
    paintBar();
    if (drawing.itemId) return;  // Saved with the bar's button.
    const form = manual.querySelector("form");
    for (const [key, value] of Object.entries(center(rect))) {
      Object.assign(form.elements[key], { value: value.toFixed(4), disabled: false });
    }
  });
  const center = (r) => ({ x: r.x + r.w / 2, y: r.y + r.h / 2, w: r.w, h: r.h });
  function paintBar() {
    const has = Boolean(editor.get());
    bar.querySelector("span").textContent = !has
      ? (drawing.itemId ? `Drag over ${drawing.label} on the photo.` : "Drag across the missing line on the photo.")
      : "Drag a corner to resize, or the box to move.";
    const done = bar.querySelector('[data-editor="done"]');
    done.textContent = drawing.itemId ? "Save box" : "Name it";
    done.hidden = !has;
  }
  function startDrawing(mode) {
    stopDrawing();
    drawing = mode;
    imgwrap.classList.add("drawing");
    document.getElementById("board-feedback").hidden = true;  // The bar takes its place.
    document.getElementById("selection-hint").textContent = mode.itemId ? `Adjust ${mode.label}’s box` : "Mark the missing line";
    const box = mode.itemId && imgwrap.querySelector(`.box[data-item="${mode.itemId}"]`);
    if (box) {  // Start from where the box is now; the original hides while it's edited.
      box.classList.add("editing");
      editor.set(Object.fromEntries(["left", "top", "width", "height"].map((k, i) => ["xywh"[i], parseFloat(box.style[k]) / 100])));
    }
    bar.hidden = false;
    paintBar();
  }
  function stopDrawing() {
    if (!drawing) return;
    drawing = null;
    imgwrap.classList.remove("drawing");
    imgwrap.querySelector(".box.editing")?.classList.remove("editing");
    editor.set(null);
    bar.hidden = true;
    paintSelection();
  }
  manual?.addEventListener("toggle", () => (manual.open ? startDrawing({}) : stopDrawing()));
  bar.querySelector('[data-editor="cancel"]').addEventListener("click", () => {
    if (drawing && !drawing.itemId) manual.open = false;  // Its toggle stops drawing.
    else stopDrawing();
  });
  bar.querySelector('[data-editor="done"]').addEventListener("click", () => {
    const rect = editor.get();
    if (!rect) return;
    if (drawing.itemId) {
      saveBox(drawing.itemId, center(rect));
      stopDrawing();
      return;
    }
    const form = manual.querySelector("form");
    form.scrollIntoView({ block: "center", behavior: "smooth" });
    form.elements.label.focus({ preventScroll: true });
  });
  async function saveBox(itemId, center) {
    const body = new FormData();
    for (const [key, value] of Object.entries(center)) body.append(key, value.toFixed(4));
    try {
      const response = await fetch(`/r/${rid}/items/${itemId}/box`, { method: "POST", body });
      const event = await response.json();
      if (!response.ok) throw new Error(event.detail || "Couldn’t move the box. Please try again.");
      moveBox(event);
      feedback(`Box moved for ${itemLabel(itemId)}.`);
    } catch (error) { feedback(error.message); }
  }
  function moveBox(event) {
    const box = imgwrap.querySelector(`.box[data-item="${event.item_id}"]`);
    if (box) { placeRect(box, event); highlight(box); }
  }

  function presence(state, text) {
    const el = document.getElementById("presence");
    el.dataset.state = state; // Styles the status dot; the text stays the accessible signal.
    el.textContent = text;
  }
  function liveEvent(event) {
    if (event.type === "presence") {
      presence("live", `Live · ${event.online} online`);
    } else if (event.type === "removed") {
      location.href = `/t/${board.dataset.tab}?removed=${rid}`;
    } else if (event.type === "box") {
      moveBox(event);
    } else if (event.type === "error") {
      feedback(event.detail || "That change couldn’t be saved.");
    } else if (!saving) { // Our own queued edits refresh once they settle.
      clearTimeout(reviewTimer);
      reviewTimer = setTimeout(refreshReview, 80);
    }
  }
  function connect() {
    if (!who || closing) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/receipts/${rid}?u=${encodeURIComponent(who)}`);
    ws.onopen = () => { presence("live", "Live updates connected"); refreshReview(); };
    ws.onmessage = (event) => liveEvent(JSON.parse(event.data));
    ws.onclose = () => {
      presence("offline", "Live updates offline · edits still save");
      if (!closing) setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
  }
  window.addEventListener("beforeunload", () => { closing = true; ws?.close(); });
  connect();
})();
