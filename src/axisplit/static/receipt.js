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

  // ---- placement ----------------------------------------------------------
  const imgwrap = document.getElementById("imgwrap");
  imgwrap.addEventListener("click", (e) => {
    const pip = e.target.closest(".pip");
    if (pip) { // remove existing token
      send({ op: "unplace", item_id: pip.closest(".box").dataset.item, username: pip.dataset.user });
      return;
    }
    const box = e.target.closest(".box");
    if (!box || !selected || !who) return;
    send({ op: "place", item_id: box.dataset.item, username: selected });
  });

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
