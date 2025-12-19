from sqlalchemy.ext.asyncio import AsyncSession
from app import crud
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
import numpy as np
from dotenv import load_dotenv
import uuid
from app import sockets_conn
import os
import datetime
from app.models import message_status,Replied_by,Vectors_base
from langchain_core.runnables import RunnableLambda,RunnableBranch
from sqlalchemy import select
from fastapi import HTTPException,logger
from app.models import reply_status

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
embeddings = OpenAIEmbeddings(model="text-embedding-3-small",api_key=api_key)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=150,api_key= api_key)


def cosine_similarity(a, b):
    a_norm = np.linalg.norm(a)
    b_norms = np.linalg.norm(b)
    a_norm = a_norm if a_norm != 0 else 1e-10
    b_norms = np.where(b_norms == 0, 1e-10, b_norms)
    return np.dot(b, a) / (a_norm * b_norms)

def mmr(query_embedding, doc_embeddings, docs, k=3, lambda_mult=0.5):
    if not docs:
        return []

    doc_embeddings = np.array(doc_embeddings, dtype=np.float32)
    query_embedding = np.array(query_embedding, dtype=np.float32)

    n_docs = doc_embeddings.shape[0]
    selected_indices = []
    candidate_indices = np.arange(n_docs)

    sim_to_query = cosine_similarity(query_embedding, doc_embeddings)

    while len(selected_indices) < min(k, n_docs):
        if not selected_indices:
            best_idx = candidate_indices[np.argmax(sim_to_query[candidate_indices])]
        else:
            selected_emb = doc_embeddings[selected_indices]
            sim_to_selected = np.max(
                np.dot(doc_embeddings[candidate_indices], selected_emb.T) /
                (np.linalg.norm(doc_embeddings[candidate_indices], axis=1)[:, None] *
                 np.linalg.norm(selected_emb, axis=1)[None, :]),
                axis=1
            )
            scores = lambda_mult * sim_to_query[candidate_indices] - (1 - lambda_mult) * sim_to_selected
            best_idx = candidate_indices[np.argmax(scores)]

        selected_indices.append(best_idx)
        candidate_indices = candidate_indices[candidate_indices != best_idx]

    return [docs[i] for i in selected_indices]

async def search_and_rerank(db: AsyncSession, query_embedding: list, top_k_candidates: int = 10, final_k: int = 3, min_sim: float = 0.1):
    distance_limit = 1 - min_sim  

    query = (
        select(Vectors_base)
        .where(Vectors_base.embedding.cosine_distance(query_embedding) <= distance_limit)
        .order_by(Vectors_base.embedding.cosine_distance(query_embedding))
        .limit(top_k_candidates)
    )

    result = await db.execute(query)
    documents = result.scalars().all()

    if not documents:
        return []

    doc_embeddings = [np.array(doc.embedding, dtype=np.float32) for doc in documents]

    selected_docs = mmr(
        query_embedding=np.array(query_embedding, dtype=np.float32),
        doc_embeddings=doc_embeddings,
        docs=documents,
        k=final_k
    )

    return selected_docs

async def chain_rerank_wrapper(inputs):
    selected_docs = await search_and_rerank(
        inputs["db"],
        inputs["query_embedding"],
        top_k_candidates=20,
        final_k=3,
        min_sim=0.1
    )
    return {**inputs, "selected_docs": selected_docs}

async def broadcasting_message(inputs):
    try:
        db = inputs["db"]
        message_id = inputs["message_id"]
        customer_id = inputs["customer_id"]
        query = inputs["query"]

        owner_manager = sockets_conn.owner_manager

        if owner_manager and owner_manager.active:
            await crud.create_customer_message(
                db,
                message_id,
                customer_id,
                query,
                status=message_status.received_by_owner,
                replier=Replied_by.not_replied,
                reply = " ",
                Reply_status=reply_status.not_replied
            )

            await owner_manager.broadcast({
                "type": "new_customer_message",
                "message_id": message_id,
                "customer_id": customer_id,
                "message": query
            })
        else:
            await crud.create_customer_message(
                db,
                message_id,
                customer_id,
                query,
                status=message_status.pending,
                replier=Replied_by.not_replied,
                reply=" ",
                Reply_status=reply_status.not_replied
            )

        return inputs

    except Exception as e:
        print(f"error {e}")
        # logger.error("Broadcasting failed", exc_info=True
        raise HTTPException(status_code=404,detail="unable to send message to admin ")
        

async def check_llm_config(inputs):
    db = inputs["db"]
    customer_id = inputs["customer_id"]
    query = inputs["query"]

    # Check room-specific configuration
    room = await crud.get_or_create_room(db, customer_id)
    message_id = str(uuid.uuid4())

    if not room.llm_enabled:
        return {
            **inputs,           
            "message_id": message_id,
            "customer_id": customer_id,
            "query": query,
            "stop": True
        }

    return {
        **inputs,
        "message_id": message_id,
        "stop": False
    }

async def rerank_docs(inputs):
    if inputs.get("stop"):
        return inputs

    docs = inputs["docs"]
    query_embedding = np.array(inputs["query_embedding"], dtype=np.float32)
    doc_embeddings = [np.array(d.embedding, dtype=np.float32) for d in docs]
    selected = mmr(
        query_embedding,
        doc_embeddings,
        docs,
        k=min(3, len(docs))
    )

    if not selected:
        selected = docs[:1]

    return {**inputs, "selected_docs": selected}

async def build_messages(inputs):
    if inputs.get("stop"):
        return inputs

    db = inputs["db"]
    customer_id = inputs["customer_id"]

    context = "\n\n".join([doc.content for doc in inputs["selected_docs"]])

    system_prompt = f"""
You are a helpful customer service agent.Which should always response as a good 
cutomer service agent.Answer the queries with a soft getsure and don't Halucinate.
and dont do counter questions to the user
Use ONLY this context:
{context}
If unrelated, reply:
"SORRY I’m not aware of this. Can you ask something related to our business?"
"""
    messages = [SystemMessage(content=system_prompt)]

    history = await crud.get_chat_history(db, customer_id, limit=5)
    for chat in history:
        messages.append(HumanMessage(content=chat.content))
        messages.append(AIMessage(content=chat.reply))

    messages.append(HumanMessage(content=inputs["query"]))

    return {**inputs, "messages": messages}

async def run_llm(inputs):
    if inputs.get("stop"):
        return inputs
    res = await llm.ainvoke(inputs["messages"])
    return {**inputs, "answer": res.content.strip()}

async def save_message(inputs):
    if inputs.get("stop"):
        return inputs["output"]

    await crud.create_customer_message(
        inputs["db"],
        inputs["message_id"],
        inputs["customer_id"],
        inputs["query"],
        status=message_status.replied,
        replier=Replied_by.llm,
        Reply_status=reply_status.delivered,
        reply=inputs["answer"],
        replied_at=datetime.datetime.utcnow()        
    )
    return inputs["answer"]
async def embed_query(inputs):
    if inputs.get("stop"):
        return inputs
    emb = await embeddings.aembed_query(inputs["query"])
    return {**inputs, "query_embedding": emb}

chain = RunnableLambda(check_llm_config)
branch_chain = RunnableBranch(
    (
        lambda x: x.get("stop") == False,
        RunnableLambda(embed_query)
        | RunnableLambda(chain_rerank_wrapper)
        | RunnableLambda(build_messages)
        | RunnableLambda(run_llm)
        | RunnableLambda(save_message)
    ),
    
        RunnableLambda(broadcasting_message)
    
)
process_chain = (
    chain | branch_chain
)
async def process_query(db, customer_id, query):
    return await process_chain.ainvoke({
        "db": db,
        "customer_id": customer_id,
        "query": query
    })