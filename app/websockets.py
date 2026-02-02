
import uuid
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.sockets_conn import customer_manager, owner_manager
from app import crud, models, service
from app.database import get_db  

router = APIRouter()
@router.websocket("/ws/customer/{customer_id}")
async def websocket_customer(websocket: WebSocket, customer_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await customer_manager.connect(customer_id, websocket)
        await crud.get_or_create_room(db, customer_id)
        
        pending_replies = await crud.get_pending_replies(db)
        for reply in pending_replies:
            try:
                print("pendind replies to ", reply.id)
                await websocket.send_json({
                    "type": "pending_message",
                    "message_id": reply.id,
                    "reply": reply.reply
                })
                await crud.mark_as_delivered(db, reply.id)
            except Exception:
                pass 
    except Exception:
        return "Unable to connect the socket"
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
                if isinstance(data, dict) and "query" in data:
                    query = data["query"]
                else:
                    query = raw_data
            except json.JSONDecodeError:
                query = raw_data
            
            # Generate ID upfront
            message_id = str(uuid.uuid4())

            # Broadcast to owner immediately that customer sent a message
            await owner_manager.broadcast({
                "type": "new_customer_message",
                "message_id": message_id,
                "customer_id": customer_id,
                "message": query,
                "timestamp": str(datetime.datetime.utcnow())
            })

            response = await service.process_query(db, customer_id, query, message_id)
            
            if isinstance(response, str):
                await websocket.send_json({
                    "type": "llm_reply",
                    "reply": response
                })
                await owner_manager.broadcast({
                    "type": "chat_update",
                    "message_id": message_id,
                    "customer_id": customer_id,
                    "query": query,
                    "reply": response
                })
            else:
                await websocket.send_json({
                    "type": "info",
                    "message": "Message sent to admin."
                })

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

            if data.get("type") == "toggle_llm":
                target_customer_id = data.get("customer_id")
                enabled = data.get("enabled")
                if target_customer_id is not None:
                    await crud.update_room_llm(db, target_customer_id, enabled)
                    await websocket.send_json({
                        "type": "system",
                        "message": f"LLM {'enabled' if enabled else 'disabled'} for customer {target_customer_id}"
                    })
                continue

            if data.get("type") != "reply":   
               continue
            message_id = data.get("message_id")
            reply_text = data.get("reply")
            # Fallback customer_id if provided (for robust delivery)
            provided_customer_id = data.get("customer_id")
            
            if not reply_text:
                print(f"Empty reply text: {data}")
                continue

            customer_id = None
            if message_id:
                customer_id = await crud.get_customer_id_by_message_id(db, message_id)
                print(f"DB lookup for msg_id={message_id} -> customer_id={customer_id}")
            
            if not customer_id and provided_customer_id:
                print(f"Using provided customer_id: {provided_customer_id}")
                customer_id = provided_customer_id

            if not customer_id:
                print("Could not find customer to reply to (DB lookup failed and no fallback)")
                continue

            if customer_id in customer_manager.active:
                print(f"Customer {customer_id} active. Sending reply.")
                await customer_manager.send_to(customer_id, {
                    "type": "owner_reply",
                    "message_id": message_id,
                    "reply": reply_text
                })
                
                # Only update DB if message_id exists
                if message_id:
                    print(f"Updating DB (Delivered) for msg_id={message_id}")
                    await crud.mark_message_as_replied(
                    db,
                    message_id,
                    reply_text,
                    models.reply_status.delivered
                    )
            else:
                print(f"Customer {customer_id} offline. Pending.")
                if message_id:
                    print(f"Updating DB (Pending) for msg_id={message_id}")
                    await crud.mark_message_as_replied(
                    db,
                    message_id,
                    reply_text,
                    models.reply_status.pending
                    )
        except WebSocketDisconnect:
           owner_manager.disconnect(owner_id, websocket)
           break

    

 