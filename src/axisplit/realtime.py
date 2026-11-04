"""Per-receipt WebSocket rooms. Server is authoritative; clients send ops and
receive broadcast events. Token ops are add/remove on a set => commutative
and idempotent, so no CRDT is needed (spec §4)."""

import json

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}

    def join(self, room: str, ws: WebSocket) -> None:
        self.rooms.setdefault(room, set()).add(ws)

    def leave(self, room: str, ws: WebSocket) -> None:
        self.rooms.get(room, set()).discard(ws)
        if not self.rooms.get(room):
            self.rooms.pop(room, None)

    async def broadcast(self, room: str, payload: dict) -> None:
        msg = json.dumps(payload)
        dead = []
        for ws in list(self.rooms.get(room, ())):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(room, ws)


hub = Hub()
