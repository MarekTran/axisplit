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
  const pending = new Set();

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
      ? `Placing ${selected}’s token · tap what they had`
      : "Choose a person, then tap what they had.";
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
  imgwrap.addEventListener("click", (event) => {
    const pip = event.target.closest("button.pip");
    if (pip) {
      send({ op: "unplace", item_id: pip.closest(".box").dataset.item, username: pip.dataset.user });
      return;
    }
    if (!who) { feedback("Choose your name before placing tokens."); return; }
    if (!selected) { feedback("Choose a person above first."); return; }
    const target = resolveItem(event.clientX, event.clientY);
    if (!target) { feedback("Detect the receipt first, or add a missing item below."); return; }
    highlight(target.el);
    send({ op: "place", item_id: target.id, username: selected });
  });

  // Delegation survives SSR review-fragment replacements.
  board.addEventListener("click", (event) => {
    const toggle = event.target.closest(".person-toggle");
    if (toggle) send({
      op: toggle.getAttribute("aria-pressed") === "true" ? "unplace" : "place",
      item_id: toggle.dataset.item, username: toggle.dataset.user,
    });
    const confirm = event.target.closest("[data-confirm]");
    if (confirm && !confirm.disabled) send({ op: confirm.dataset.confirm });
    const jump = event.target.closest("[data-jump]");
    if (jump) {
      const box = imgwrap.querySelector(`.box[data-item="${jump.dataset.jump}"]`);
      if (box) { box.scrollIntoView({ block: "center", behavior: "smooth" }); highlight(box); }
    }
  });

  function itemLabel(id) {
    return board.querySelector(`.item-review li[data-item="${id}"] .item-heading > strong`)?.textContent || "item";
  }
  async function send(op, allowUndo = true) {
    if (!who) { location.href = `/claim?next=${encodeURIComponent(location.pathname)}`; return; }
    const key = `${op.op}:${op.item_id || ""}:${op.username || ""}`;
    if (pending.has(key)) return;
    pending.add(key);
    feedback("Saving…");
    const label = itemLabel(op.item_id);
    // Remember whether this is a true change (placing an existing token is a no-op).
    const box = op.item_id && imgwrap.querySelector(`.box[data-item="${op.item_id}"]`);
    const hadToken = box && [...box.querySelectorAll(".pip")].some(p => p.dataset.user === op.username);
    try {
      const response = await fetch(`/r/${rid}/ops`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...op, actor: who }),
      });
      const event = await response.json();
      if (!response.ok) throw new Error(event.detail || "Couldn’t save. Please try again.");
      if (op.op === "place" || op.op === "unplace") {
        const changed = op.op === "place" ? !hadToken : hadToken;
        const inverse = allowUndo && changed ? { ...op, op: op.op === "place" ? "unplace" : "place" } : null;
        feedback(`${op.username} ${op.op === "place" ? "added to" : "removed from"} ${label}. Saved.`, inverse);
      } else {
        feedback(op.op === "confirm" ? "Your confirmation is saved. Others can now check their shares." : "Your confirmation was withdrawn.");
      }
      await refreshReview();
    } catch (error) {
      feedback(error.message || "Connection lost. Your change wasn’t confirmed; please retry.");
    } finally {
      pending.delete(key);
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
      if (version !== reviewVersion) return;
      const next = new DOMParser().parseFromString(html, "text/html").getElementById("review");
      // Preserve keyboard focus when a control is replaced by server-rendered HTML.
      const active = document.activeElement;
      const focus = active?.closest("#review") ? {
        item: active.dataset.item, user: active.dataset.user, confirm: active.dataset.confirm,
      } : null;
      document.getElementById("review").replaceWith(next);
      if (focus) {
        const replacement = [...next.querySelectorAll("button")].find(button => focus.confirm
          ? button.dataset.confirm : button.dataset.item === focus.item && button.dataset.user === focus.user);
        replacement?.focus({ preventScroll: true });
      }
      const status = document.getElementById("status");
      status.textContent = next.dataset.statusLabel;
      status.className = `badge ${next.dataset.status}`;
      syncBoxTokens();
    } catch (error) { feedback(error.message); }
  }
  function syncBoxTokens() {
    for (const box of imgwrap.querySelectorAll(".box")) {
      const holders = [...board.querySelectorAll(`.person-toggle[data-item="${box.dataset.item}"]`)]
        .filter(button => button.getAttribute("aria-pressed") === "true");
      const tokens = box.querySelector(".tokens");
      tokens.replaceChildren();
      for (const holder of holders) {
        const user = holder.dataset.user;
        const pip = document.createElement("button");
        pip.type = "button";
        pip.className = "pip";
        pip.dataset.user = user;
        pip.style.setProperty("--c", colors[user] || "#888");
        pip.textContent = user.slice(0, 1);
        pip.setAttribute("aria-label", `Remove ${user} from ${itemLabel(box.dataset.item)}`);
        pip.title = `Remove ${user}`;
        tokens.append(pip);
      }
      box.classList.toggle("assigned", holders.length > 0);
    }
  }
  function liveEvent(event) {
    if (event.type === "presence") {
      document.getElementById("presence").textContent = `Live · ${event.online} online`;
    } else if (event.type === "removed") {
      location.href = `/t/${board.dataset.tab}?removed=${rid}`;
    } else if (event.type === "error") {
      feedback(event.detail || "That change couldn’t be saved.");
    } else {
      clearTimeout(reviewTimer);
      reviewTimer = setTimeout(refreshReview, 80);
    }
  }
  function connect() {
    if (!who || closing) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/receipts/${rid}?u=${encodeURIComponent(who)}`);
    ws.onopen = () => { document.getElementById("presence").textContent = "Live updates connected"; refreshReview(); };
    ws.onmessage = (event) => liveEvent(JSON.parse(event.data));
    ws.onclose = () => {
      document.getElementById("presence").textContent = "Live updates offline · edits still save";
      if (!closing) setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
  }
  window.addEventListener("beforeunload", () => { closing = true; ws?.close(); });
  connect();
})();
