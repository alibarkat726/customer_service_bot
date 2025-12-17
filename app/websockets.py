
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.sockets_conn import customer_manager, owner_manager
from app import crud,models
from app.database import get_db  

router = APIRouter()

@router.websocket("/ws/customer/{customer_id}")
async def websocket_customer(websocket: WebSocket, customer_id: int,db:AsyncSession= Depends(get_db)):
    try:
        await customer_manager.connect(customer_id, websocket)
        pending_replies = await crud.get_pending_replies(db)
        for reply in pending_replies:
            try:
                print("pendind replies to ", reply.id)
                await websocket.send_json({
                    "type": "pending_message",
                    "message_id": reply.id,
                    "reply":reply.reply
            })
                await crud.mark_as_delivered(db,reply.id)
            except Exception:
                return "Unable to mark as delivered"
    
    
    except Exception:
        return "Unable to connect the socket"
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        customer_manager.disconnect(customer_id)


@router.websocket("/ws/owner/{owner_id}")
async def websocket_owner(websocket: WebSocket, owner_id: int, db: AsyncSession = Depends(get_db)):
    await owner_manager.connect(owner_id, websocket)
    pending_messages = await crud.get_pending_messages(db)
    for msg in pending_messages:
        try:
            print("Sending pending message:", msg.id)
            await websocket.send_json({
                "type": "pending_message",
                "message_id": msg.id,
                "customer_id": msg.customer_id,
                "message": msg.content
            })
            await crud.mark_as_owner_received(db,msg.id)
        except Exception:
            break

    while True:
        try:
            data = await websocket.receive_json()

            if data.get("type") != "reply":   
               continue

            message_id = data.get("message_id")
            reply_text = data.get("reply")

            if not message_id or not reply_text:
                await websocket.send_json({
                    "type": "error",
                    "message": "message_id and reply are required"
               })
                continue

            customer_id = await crud.get_customer_id_by_message_id(db, message_id)
            if not customer_id:
               continue

            if customer_id in customer_manager.active:
                await customer_manager.send_to(customer_id, {
                    "type": "owner_reply",
                    "message_id": message_id,
                    "reply": reply_text
               })
                await crud.mark_message_as_replied(
                   db,
                   message_id,
                   reply_text,
                   models.reply_status.delivered
               )
            else:
                await crud.mark_message_as_replied(
                   db,
                   message_id,
                   reply_text,
                   models.reply_status.pending
               )
        except WebSocketDisconnect:
           owner_manager.disconnect(owner_id, websocket)
           break

        # except Exception as e:
        #    await websocket.send_json({
        #        "type": "error",
        #        "message": "Failed to process reply"
        #    })

 