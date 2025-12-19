from sqlalchemy import Column, Integer, String, Boolean, Text, Enum as pyenum, Index
from pgvector.sqlalchemy import Vector
import datetime
from sqlalchemy import DateTime
from app.database import Base
from enum import Enum

class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True, index=True)
    llm_enabled = Column(Boolean, default=True)

class Vectors_base(Base):
    __tablename__ = "Vectors"
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536))
    __table_args__ = (
        Index("idx_knowledge_content", "content"),
    )

class message_status(str, Enum):
    pending = "pending"
    received_by_owner = "received_by_owner"
    replied = "replied"

class Replied_by(str, Enum):
    admin = "admin"
    llm = "llm"
    not_replied = "not_replied"

class reply_status(str,Enum):
    delivered = "delivered"
    pending = "pending"
    not_replied = "not_replied"
class CustomerMessage(Base):
    __tablename__ = "customer_messages"

    id = Column(String, primary_key=True)
    customer_id = Column(Integer, index=True, nullable=False)
    message_status = Column(pyenum(message_status), index=True, nullable=False)
    content = Column(Text, nullable=False)
    reply = Column(Text, nullable=True)
    reply_status = Column(pyenum(reply_status), index =True,nullable = False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    replied_at = Column(DateTime, nullable=True)
    replied_by = Column(pyenum(Replied_by),index=True,nullable=False)
    __table_args__ = (
        Index("idx_cust_status", "customer_id", "message_status"),
    )





class Room(Base):
    __tablename__ = "rooms"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, unique=True, index=True, nullable=False)
    llm_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
