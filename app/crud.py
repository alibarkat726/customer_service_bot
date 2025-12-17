from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import datetime
from app.models import Vectors_base,SystemConfig
from pgvector.sqlalchemy import Vector
import hashlib

from app.models import CustomerMessage,message_status,Replied_by,reply_status
async def get_system_config(db: AsyncSession):
    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    config = result.scalars().first()
    if not config:
        config = SystemConfig(id=1, llm_enabled=True)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config

async def set_llm_enabled(db: AsyncSession, enabled: bool):
    config = await get_system_config(db)
    config.llm_enabled = enabled
    await db.commit()
    await db.refresh(config)
    return config

async def add_document(db: AsyncSession, content: str, embedding: list):
    doc = Vectors_base(content=content, embedding=embedding)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc

async def add_many_documents(db: AsyncSession, docs_data: list[dict]):
    docs = [Vectors_base(content=d["content"], embedding=d["embedding"]) for d in docs_data]
    db.add_all(docs)
    await db.commit()
    for doc in docs:
        await db.refresh(doc)
    return docs

def hash_query(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()

#Websocket functions 
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models import CustomerMessage
import datetime

async def create_customer_message(db: AsyncSession,message_id: str, customer_id: int, content: str,status:message_status,replier:Replied_by,reply:str,Reply_status:reply_status):
    msg = CustomerMessage(
        id=message_id,
        message_status =status,
        customer_id=customer_id,
        content=content,
        reply =reply,
        replied_by = replier,
        reply_status =Reply_status,
        created_at=datetime.datetime.utcnow()
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def get_customer_id_by_message_id(db: AsyncSession, message_id: str):
    query = select(CustomerMessage).where(CustomerMessage.id == message_id)
    res = await db.execute(query)
    msg = res.scalar_one_or_none()
    return msg.customer_id if msg else None

async def get_customer_messages(customer_id:int,db:AsyncSession):
    query = select(CustomerMessage).where(CustomerMessage.customer_id == customer_id)
    result = await db.execute(query)
    messages = result.scalars().all()
    if not messages:
        return {"message": "No messages found for this customer", "data": []}

    return {"data": messages}
async def mark_as_owner_received(
    db: AsyncSession,
    message_id: int
):
    data = (
        update(CustomerMessage)
        .where(
            CustomerMessage.id == message_id,
            CustomerMessage.message_status == message_status.pending
        )
        .values(message_status=message_status.received_by_owner)
    )

    await db.execute(data)
    await db.commit()

async def mark_as_delivered(
           db: AsyncSession,
         message_id: int
):
    data = (
        update(CustomerMessage)
        .where(
            CustomerMessage.id == message_id,
            CustomerMessage.reply_status == reply_status.pending
        )
        .values(reply_status=reply_status.delivered)
    )

    await db.execute(data)
    await db.commit()      
async def mark_message_as_replied(db: AsyncSession, message_id: str, reply: str,reply_status:reply_status):
    try:
        customer_id = await get_customer_id_by_message_id(db, message_id)
        if not customer_id:
            raise RuntimeError("Unable to mark message as replied")

        query = (
            update(CustomerMessage)
            .where(CustomerMessage.id == message_id)
            .values(
                reply=reply,
                replied_by=Replied_by.admin,
                message_status=message_status.replied,  
                replied_at=datetime.datetime.utcnow(),
                reply_status = reply_status
            )
        )
        await db.execute(query)
        await db.commit()
        return "Message marked as replied"
    except Exception as e:
        await db.rollback() 
        return f"Unable to set message as replied: {str(e)}"


async def get_chat_history(db: AsyncSession, customer_id: int, limit: int = 5):
    query = (
        select(CustomerMessage)
        .where(CustomerMessage.customer_id == customer_id)
        .order_by(CustomerMessage.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_pending_messages(db: AsyncSession):
    query = select(CustomerMessage).where(CustomerMessage.message_status == message_status.pending)
    res = await db.execute(query)
    return res.scalars().all()


async def get_pending_replies(db:AsyncSession):
    query = select(CustomerMessage).where(CustomerMessage.reply_status == reply_status.pending)
    response = await db.execute(query)
    return response.scalars().all()



