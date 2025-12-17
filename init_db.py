import asyncio
from app.database import engine, Base
from app.models import SystemConfig, KnowledgeBase
from sqlalchemy import text

async def init_models():
    async with engine.begin() as conn:
        # Create pgvector extension if not exists
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized.")

if __name__ == "__main__":
    asyncio.run(init_models())
