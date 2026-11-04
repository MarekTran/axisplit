from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import Session
from ..deps import ensure_membership, get_tab_or_404
from ..models import Receipt
from ..ops import apply_op
from ..realtime import hub

router = APIRouter()


@router.websocket("/ws/receipts/{receipt_id}")
async def receipt_ws(websocket: WebSocket, receipt_id: str, u: str = ""):
    await websocket.accept()
    async with Session() as session:
        receipt = await session.get(Receipt, receipt_id)
        if receipt is None or not u.strip():
            await websocket.close(code=4404)
            return
        tab = await get_tab_or_404(session, receipt.tab_id)
        # username-is-ID v1: connecting with a name inside a tab joins it
        await ensure_membership(session, tab.id, u.strip()[:32])
    hub.join(receipt_id, websocket)
    await hub.broadcast(receipt_id, {"type": "presence", "username": u, "online": len(hub.rooms.get(receipt_id, ()))})
    try:
        while True:
            body = await websocket.receive_json()
            async with Session() as session:
                tab2 = await get_tab_or_404(session, tab.id)
                try:
                    event = await apply_op(session, tab2, receipt_id, {**body, "actor": u})
                except Exception as e:  # keep the socket alive on bad ops
                    await websocket.send_json({"type": "error", "detail": str(e)})
                    continue
            event.setdefault("actor", u)
            await hub.broadcast(receipt_id, event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(receipt_id, websocket)
        await hub.broadcast(receipt_id, {"type": "presence", "username": u,
                                         "online": len(hub.rooms.get(receipt_id, ()))})
