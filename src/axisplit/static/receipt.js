// Token board: tap-to-select token, tap box to place (touch-first, spec §7).
// Live sync via WebSocket; HTTP fallback if WS unavailable. Ops are
// add/remove on a set => idempotent, so applying our own echo is harmless.
(() => {
  const board = document.getElementById("board");
  if (!board) return;
  const rid = board.dataset.receipt;
  const who = board.dataset.who;
  const colors = JSON.parse(board.dataset.colors);

  let selected = who && colors[who] ? who : null;
  let ws = null;

  // ---- tokenbar selection -------------------------------------------------
  const tokenbar = document.getElementById("tokenbar");
  const paint = () =>
    tokenbar.querySelectorAll(".token").forEach(
      (t) => t.classList.toggle("selected", t.dataset.user === selected));
  tokenbar.addEventListener("click", (e) => {
    const t = e.target.closest(".token");
    if (!t) return;
    selected = t.dataset.user === selected ? null : t.dataset.user;
    paint();
  });
  paint();

  // ---- placement: tap anywhere on the photo -----------------------------
  // Token snaps to the closest item: generous containment first (bbox
  // expanded 4%), else the nearest box center at any distance. Toast only
  // when the receipt has no items at all.
  const imgwrap = document.getElementById("imgwrap");
  const PAD = 0.04; // expand each bbox by 4% of image size on all sides

  function normPoint(clientX, clientY) {
    const img = imgwrap.querySelector("img");
    const r = img.getBoundingClientRect();
    return [(clientX - r.left) / r.width, (clientY - r.top) / r.height];
  }

  function resolveItem(clientX, clientY) {
    const boxes = imgwrap.querySelectorAll(".box");
    if (!boxes.length) return null;
    const [px, py] = normPoint(clientX, clientY);
    let nearest = null, nearestD = Infinity;
    for (const el of boxes) {
      const x = parseFloat(el.style.left) / 100, y = parseFloat(el.style.top) / 100;
      const w = parseFloat(el.style.width) / 100, h = parseFloat(el.style.height) / 100;
      if (px >= x - PAD && px <= x + w + PAD && py >= y - PAD && py <= y + h + PAD) {
        return { id: el.dataset.item, el };  // inside (generous) bbox: immediate match
      }
      const d = Math.hypot(px - (x + w / 2), py - (y + h / 2));
      if (d < nearestD) [nearest, nearestD] = [{ id: el.dataset.item, el }, d];
    }
    return nearest;  // else just the closest item, however far
  }

  imgwrap.addEventListener("click", (e) => {
    const pip = e.target.closest(".pip");
    if (pip) { // remove existing token
      send({ op: "unplace", item_id: pip.closest(".box").dataset.item, username: pip.dataset.user });
      return;
    }
    if (!selected || !who) return;
    const target = resolveItem(e.clientX, e.clientY);
    if (!target) {
      openQuickAdd(...normPoint(e.clientX, e.clientY));
      return;
    }
    // immediate local echo: drop marker at the tap point + box highlight
    dropMarker(e.clientX, e.clientY, selected);
    target.el.classList.add("hit");
    setTimeout(() => target.el.classList.remove("hit"), 500);
    send({ op: "place", item_id: target.id, username: selected });
  });

  const noMatch = document.createElement("div");
  noMatch.className = "no-match";
  noMatch.textContent = "no items yet — tap where a line is printed to add it";
  function flashNoMatch(e) {
    const [nx, ny] = normPoint(e.clientX, e.clientY);
    noMatch.style.left = `${nx * 100}%`;
    noMatch.style.top = `${ny * 100}%`;
    imgwrap.append(noMatch);
    noMatch.classList.remove("show");
    void noMatch.offsetWidth;
    noMatch.classList.add("show");
  }

  // Tap-to-add: while the receipt has no items (stub OCR / OCR missed the
  // whole photo), a tap opens a mini form at the tap point; the created box
  // is centered on that point so subsequent token taps snap to it correctly.
  function openQuickAdd(nx, ny) {
    let qa = document.getElementById("quickadd");
    if (qa) qa.remove();
    qa = document.createElement("form");
    qa.id = "quickadd";
    qa.method = "post";
    qa.action = `/r/${rid}/items`;
    qa.innerHTML = `
      <input name="label" placeholder="line as printed" required maxlength="120" autofocus>
      <div class="qa-row">
        <input name="price" placeholder="price" required inputmode="decimal">
        <input type="hidden" name="x" value="${nx.toFixed(4)}">
        <input type="hidden" name="y" value="${ny.toFixed(4)}">
        <button type="submit">Add</button>
        <button type="button" class="qa-cancel">✕</button>
      </div>`;
    qa.style.left = `${Math.min(nx, 0.55) * 100}%`;
    qa.style.top = `${Math.min(ny, 0.8) * 100}%`;
    qa.querySelector(".qa-cancel").addEventListener("click", () => qa.remove());
    qa.addEventListener("click", (e) => e.stopPropagation());
    qa.addEventListener("submit", (e) => e.stopPropagation());
    imgwrap.append(qa);
    qa.querySelector("input[name=label]").focus();
  }

  function dropMarker(clientX, clientY, username) {
    const img = imgwrap.querySelector("img");
    const r = img.getBoundingClientRect();
    const m = document.createElement("span");
    m.className = "pip drop-marker";
    m.style.position = "absolute";
    m.style.left = `${clientX - r.left - 8}px`;
    m.style.top = `${clientY - r.top - 8}px`;
    m.style.setProperty("--c", colors[username] || "#888");
    imgwrap.append(m);
    m.addEventListener("animationend", () => m.remove());
  }

  function send(op) {
    if (!who) return location = `/claim?next=${encodeURIComponent(location.pathname)}`;
    const body = { ...op, actor: who };
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(body)); // reply comes back via broadcast
    } else {
      fetch(`/r/${rid}/ops`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }).then((r) => r.json()).then((ev) => apply(ev, /*local*/ true));
    }
  }

  document.getElementById("confirm-btn").addEventListener("click", () =>
    send({ op: "confirm" }));

  // ---- live apply ---------------------------------------------------------
  function tokensOf(itemId) {
    return [...document.querySelector(`.box[data-item="${itemId}"]`).querySelectorAll(".pip")]
      .map((p) => p.dataset.user);
  }
  function apply(ev, optimistic) {
    if (ev.type === "place" || ev.type === "unplace") {
      const box = document.querySelector(`.box[data-item="${ev.item_id}"]`);
      if (!box) return;
      const holder = box.querySelector(".tokens");
      const have = [...holder.querySelectorAll(".pip")];
      const existing = have.find((p) => p.dataset.user === ev.username);
      if (ev.type === "place") {
        if (!existing) {
          const pip = document.createElement("span");
          pip.className = "pip flash";
          pip.dataset.user = ev.username;
          pip.style.setProperty("--c", colors[ev.username] || "#888");
          holder.append(pip);
        }
      } else if (existing) {
        existing.remove();
      }
    }
    if (ev.status) setStatus(ev.status);
    if (ev.type === "presence") {
      document.getElementById("presence").textContent = `${ev.online} online`;
    }
  }
  function setStatus(s) {
    const el = document.getElementById("status");
    el.textContent = s;
    document.getElementById("confirm-btn").textContent =
      s === "settled" ? "Settled ✓" : "Confirm split";
  }

  // ---- websocket ----------------------------------------------------------
  function connect() {
    if (!who) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/receipts/${rid}?u=${encodeURIComponent(who)}`);
    ws.onmessage = (e) => apply(JSON.parse(e.data));
    ws.onclose = () => setTimeout(connect, 2000);
    ws.onerror = () => ws.close();
  }
  connect();
})();
