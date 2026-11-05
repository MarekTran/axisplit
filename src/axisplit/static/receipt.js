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

  function feedback(message, undoAction = null) {
    const toast = document.getElementById("board-feedback");
    toast.querySelector("span").textContent = message;
    toast.hidden = false;
    undo = undoAction;
    document.getElementById("undo-token").hidden = !undoAction;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { toast.hidden = true; undo = null; }, 8000);
  }
  document.getElementById("undo-token").addEventListener("click", () => {
    const action = undo;
    undo = null;
    if (action) send(action, false);
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
    const items = [...imgwrap.querySelectorAll(".box")].map((el) => ({
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
  function place(itemId) {
    if (!who) { feedback("Choose your name before placing tokens."); return; }
    if (!selected) { feedback("Choose a person above first."); return; }
    send({ op: "place", item_id: itemId, username: selected });
  }
  imgwrap.addEventListener("click", (event) => {
    const pip = event.target.closest("button.pip");
    if (pip) {
      send({ op: "unplace", item_id: pip.closest(".box").dataset.item, username: pip.dataset.user });
      return;
    }
    const target = resolveItem(event.clientX, event.clientY);
    if (!target) { feedback("Detect the receipt first, or add a missing item below."); return; }
    if (who && selected) highlight(target.el);
    place(target.id);
  });

  // Delegation survives SSR review-fragment replacements.
  board.addEventListener("click", (event) => {
    // List rows mirror the photo: a row adds the selected person, a holder chip takes theirs off.
    const add = event.target.closest("[data-add]");
    if (add) place(add.dataset.add);
    const holder = event.target.closest(".holder");
    if (holder) send({ op: "unplace", item_id: holder.dataset.item, username: holder.dataset.user });
    const confirm = event.target.closest("[data-confirm]");
    if (confirm && !confirm.disabled) send({ op: confirm.dataset.confirm });
    const jump = event.target.closest("[data-jump]");
    if (jump) {
      const box = imgwrap.querySelector(`.box[data-item="${jump.dataset.jump}"]`);
      if (box) { box.scrollIntoView({ block: "center", behavior: "smooth" }); highlight(box); }
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
      renderTokens(op.item_id, counts); // Show the new count right away; the server's review replaces it.
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
      syncBoxTokens();
    } catch (error) { feedback(error.message); }
  }
  // Same order and markup as review.html and receipt.html: sorted by name, count shown from 2 up.
  function tokenCounts(itemId) {
    return new Map([...board.querySelectorAll(`.holder[data-item="${itemId}"]`)]
      .map(holder => [holder.dataset.user, Number(holder.dataset.count)]));
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
  function renderPips(itemId, counts) {
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
    box.classList.toggle("assigned", placed.length > 0);
  }
  function renderTokens(itemId, counts) {
    renderPips(itemId, counts);
    const row = board.querySelector(`.item-review li[data-item="${itemId}"]`);
    const label = itemLabel(itemId);
    const placed = [...counts].filter(([, n]) => n > 0).sort(byName);
    const chips = placed.map(([user, n]) => {
      const chip = tokenButton("holder", user, label);
      Object.assign(chip.dataset, { item: itemId, count: n });
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
    for (const box of imgwrap.querySelectorAll(".box")) renderPips(box.dataset.item, tokenCounts(box.dataset.item));
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
