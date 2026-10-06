import json
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent.llm_agent import RealLLMSpatialAgent

app = FastAPI(title="Spatial-Copilot API", description="AI Agent with Real Tool Calling and Spatial Sandbox")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = RealLLMSpatialAgent()


class QueryRequest(BaseModel):
    query: str
    api_key: Optional[str] = ""
    base_url: Optional[str] = ""
    model: Optional[str] = ""


@app.post("/api/query/stream")
async def stream_query(req: QueryRequest):
    async def event_generator():
        async for chunk in agent.run_stream(
            query=req.query,
            api_key=req.api_key,
            base_url=req.base_url,
            model=req.model,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
