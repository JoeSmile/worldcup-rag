import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import chat
from etl.data.db import execute_query

logger = logging.getLogger(__name__)

app = FastAPI(title="World Cup RAG API")

# 允许跨域（小程序/Web 都能调）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    history: Optional[List[Dict[str, str]]] = None

class ChatResponse(BaseModel):
    answer: str
    tool_name: Optional[str] = None
    tools_used: Optional[List[str]] = None
    sql_generated: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    workflow: Optional[str] = None
    router_choice: Optional[str] = None
    auto_routed: Optional[bool] = None

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query cannot be empty")

    try:
        return await run_in_threadpool(chat, query, request.history)
    except Exception as exc:
        logger.exception("chat request failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    try:
        result = await run_in_threadpool(execute_query, "SELECT 1")
    except Exception as exc:
        logger.exception("database readiness check failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready", "database": result[0][0] == 1}

if __name__ == "__main__":
    import uvicorn

    from core.config import settings

    uvicorn.run(app, host=settings.app_host, port=settings.app_port)