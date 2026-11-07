from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import Session
from ..models import Receipt
from ..ops import apply_op
from ..realtime import hub

router = APIRouter()


@router.websocket("/ws/receipts/{receipt_id}")
async def receipt_ws(websocket: WebSocket, receipt_id: str):
    await websocket.accept()
    async with Session() as session:
        if await session.get(Receipt, receipt_id) is None:
            await websocket.close(code=4404)
            return
    hub.join(receipt_id, websocket)
    await hub.broadcast(receipt_id, {"type": "presence", "online": len(hub.rooms.get(receipt_id, ()))})
    try:
        while True:
            body = await websocket.receive_json()
            async with Session() as session:
                try:
                    event = await apply_op(session, await session.get(Receipt, receipt_id), body)
                except Exception as e:  # keep the socket alive on bad ops
                    await websocket.send_json({"type": "error", "detail": str(e)})
                    continue
            await hub.broadcast(receipt_id, event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(receipt_id, websocket)
        await hub.broadcast(receipt_id, {"type": "presence", "online": len(hub.rooms.get(receipt_id, ()))})
