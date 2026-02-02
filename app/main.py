from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from app import crud, service,database
from pydantic import BaseModel
from typing import List
from contextlib import asynccontextmanager
from app.websockets import router
from app.database import init_db,engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(engine)
    yield
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router=router)
class QueryRequest(BaseModel):
    query: str

class DocRequest(BaseModel):
    content: str

class BatchDocRequest(BaseModel):
    documents: List[str]

@app.get("/")
async def root():
    return {"message": "running"}

@app.get("/messages")
async def get_customer_messages(customer_id:int,db:AsyncSession = Depends(database.get_db)):
    try:
        response = await crud.get_customer_messages(customer_id,db)
        return response
    except Exception as e:
        print(f"Unable to find messages {e}")
# @app.post("/ask")
# async def ask_question(request: QueryRequest,customer_id:int, db: AsyncSession = Depends(database.get_db)):
#     response = await service.process_query(db,customer_id,request.query)
#     return {"response": response}

@app.post("/llm/enable")
async def enable_llm(enabled: bool, db: AsyncSession = Depends(database.get_db)):
    config = await crud.set_llm_enabled(db, enabled)
    return {"llm_enabled": config.llm_enabled}

@app.post("/admin/add/one")
async def ingest_document(request: DocRequest, db: AsyncSession = Depends(database.get_db)):
    embedding = await service.embeddings.aembed_query(request.content)
    doc = await crud.add_document(db, request.content, embedding)
    return {"id": doc.id, "message": "Document added successfully"}

@app.post("/admin/add/many")
async def add_many_documents(request: BatchDocRequest, db: AsyncSession = Depends(database.get_db)):
    embeddings = await service.embeddings.aembed_documents(request.documents)
    docs_data = [
        {"content": content, "embedding": emb} 
        for content, emb in zip(request.documents, embeddings)
    ]
    await crud.add_many_documents(db, docs_data)
    return {"message": f"{len(docs_data)} documents added successfully"}

@app.get("/admin/customers")
async def get_all_customers(db: AsyncSession = Depends(database.get_db)):
    customers = await crud.get_all_customers_active(db)
    return customers
