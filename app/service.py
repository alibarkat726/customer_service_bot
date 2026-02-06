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
from sqlalchemy import select
from fastapi import HTTPException,logger
from app.models import reply_status
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
embeddings = OpenAIEmbeddings(model="text-embedding-3-small",api_key=api_key)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=150,api_key= api_key)

class GraphState(TypedDict):
    db: AsyncSession
    customer_id: int
    query: str
    message_id: Optional[str]
    stop: Optional[bool]
    query_embedding: Optional[list]
    selected_docs: Optional[list]
    messages: Optional[list]
    answer: Optional[str]


def cosine_similarity(a, b):
    a_norm = np.linalg.norm(a)
    b_norms = np.linalg.norm(b)
    a_norm = a_norm if a_norm != 0 else 1e-10
    b_norms = np.where(b_norms == 0, 1e-10, b_norms)
    return np.dot(b, a) / (a_norm * b_norms)

def mmr(query_embedding, doc_embeddings, docs,k, lambda_mult=0.5):
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

async def search_and_rerank(db: AsyncSession, query_embedding: list, top_k_candidates: int = 20, final_k: int = 10, min_sim: float = 0.1):
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

async def chain_rerank_wrapper(state: GraphState) -> GraphState:
    selected_docs = await search_and_rerank(
        state["db"],
        state["query_embedding"],
        top_k_candidates=20,
        final_k=20,
        min_sim=0.1
    )
    return {**state, "selected_docs": selected_docs}

async def broadcasting_message(state: GraphState) -> GraphState:
    try:
        db = state["db"]
        message_id = state["message_id"]
        customer_id = state["customer_id"]
        query = state["query"]
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
        return state
    except Exception as e:
        print(f"error {e}")
        raise HTTPException(status_code=404,detail="unable to send message to admin ")
        
async def check_llm_config(state: GraphState) -> GraphState:
    db = state["db"]
    customer_id = state["customer_id"]
    query = state["query"]

    
    room = await crud.get_or_create_room(db, customer_id)
    message_id = state.get("message_id") or str(uuid.uuid4())

    if not room.llm_enabled:
        return {
            **state,           
            "message_id": message_id,
            "customer_id": customer_id,
            "query": query,
            "stop": True
        }
    return {
        **state,
        "message_id": message_id,
        "stop": False
    }
async def rerank_docs(state: GraphState) -> GraphState:
    if state.get("stop"):
        return state

    docs = state["docs"]
    query_embedding = np.array(state["query_embedding"], dtype=np.float32)
    doc_embeddings = [np.array(d.embedding, dtype=np.float32) for d in docs]
    selected = mmr(
        query_embedding,
        doc_embeddings,
        docs,
        k=min(5, len(docs))
    )
    if not selected:
        selected = docs[:1]

    return {**state, "selected_docs": selected}

async def build_messages(state: GraphState) -> GraphState:
    if state.get("stop"):
        return state

    db = state["db"]
    customer_id = state["customer_id"]

    context = "\n\n".join([doc.content for doc in state["selected_docs"]])

    system_prompt = f"""
You are a polite and professional customer support representative for our business.

Your role:
- Respond like a real human staff member, not a chatbot.
- Be warm, calm, and respectful.
- Give clear and complete answers using the provided context.
- Do NOT make assumptions or invent information.
- Do NOT hallucinate.

Conversation rules:
- Do NOT ask follow-up questions unless clarification is genuinely required.
- Do NOT end every response with phrases like "How can I help you further?" or similar.
- If the user's question is fully answered, stop naturally.
- Avoid repetitive or scripted responses.

Knowledge rules:
- Use this context to answer:
{context}
And make sure to answer with a god structure.

If the question is unrelated to the business or cannot be answered using the context, reply exactly with:
"SORRY, I'm not aware of this. Can you ask something related to our business?"

Tone:
- Friendly but not overly enthusiastic
- Natural and human
- Short and helpful, like an in-store staff member
"""
    messages = [SystemMessage(content=system_prompt)]

    history = await crud.get_chat_history(db, customer_id, limit=5)
    for chat in history:
        messages.append(HumanMessage(content=chat.content))
        messages.append(AIMessage(content=chat.reply))

    messages.append(HumanMessage(content=state["query"]))

    return {**state, "messages": messages}

async def run_llm(state: GraphState) -> GraphState:
    if state.get("stop"):
        return state
    res = await llm.ainvoke(state["messages"])
    return {**state, "answer": res.content.strip()}

async def save_message(state: GraphState) -> GraphState:
    if state.get("stop"):
        return state

    await crud.create_customer_message(
        state["db"],
        state["message_id"],
        state["customer_id"],
        state["query"],
        status=message_status.replied,
        replier=Replied_by.llm,
        Reply_status=reply_status.delivered,
        reply=state["answer"],
        replied_at=datetime.datetime.utcnow()        
    )
    return state
async def embed_query(state: GraphState) -> GraphState:
    if state.get("stop"):
        return state
    emb = await embeddings.aembed_query(state["query"])
    return {**state, "query_embedding": emb}


def route_after_check(state: GraphState) -> str:
    """Route to either broadcast or embed based on stop flag"""
    if state.get("stop"):
        return "broadcast"
    else:
        return "embed"


workflow = StateGraph(GraphState)

workflow.add_node("check_llm_config", check_llm_config)
workflow.add_node("broadcast", broadcasting_message)
workflow.add_node("embed", embed_query)
workflow.add_node("search", chain_rerank_wrapper)
workflow.add_node("build_msg", build_messages)
workflow.add_node("llm", run_llm)
workflow.add_node("save", save_message)
workflow.set_entry_point("check_llm_config")
workflow.add_conditional_edges(
    "check_llm_config",
    route_after_check,
    {
        "broadcast": "broadcast",
        "embed": "embed"
    }
)
workflow.add_edge("embed", "search")
workflow.add_edge("search", "build_msg")
workflow.add_edge("build_msg", "llm")
workflow.add_edge("llm", "save")
workflow.add_edge("save", END)

workflow.add_edge("broadcast", END)
graph = workflow.compile()

async def process_query(db, customer_id, query, message_id=None):
    result = await graph.ainvoke({
        "db": db,
        "customer_id": customer_id,
        "query": query,
        "message_id": message_id
    })
    return result.get("answer")