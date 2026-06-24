import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import chat
from tools import execute_sql

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

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query cannot be empty")

    try:
        answer = await run_in_threadpool(chat, query, request.history)
        return {"answer": answer}
    except Exception as exc:
        logger.exception("chat request failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    try:
        result = await run_in_threadpool(execute_sql, "SELECT 1")
    except Exception as exc:
        logger.exception("database readiness check failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready", "database": result[0][0] == 1}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)