import json
from contextlib import aclosing
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.llm_agent import RealLLMSpatialAgent
from agent.validator import AnalysisPoint

app = FastAPI(title="Spatial-Copilot 演示 API", version="0.1.0")
agent = RealLLMSpatialAgent()
CLIENT = Path(__file__).parent
app.mount("/vendor", StaticFiles(directory=CLIENT / "vendor"), name="vendor")


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    query: str = Field(min_length=1, max_length=4000)
    api_key: str = Field(default="", max_length=4096)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=200)
    mode: Literal["local", "remote", "auto"] = "local"
    custom_point: Optional[AnalysisPoint] = None
    distance_meters: Optional[float] = Field(default=None, gt=0, le=50000)
    min_capacity: Optional[int] = Field(default=None, ge=0, le=10000000)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("查询内容不能为空")
        return value.strip()


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    # 不回显原始请求，避免密钥及 NaN 出现在验证响应中。
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})


@app.get("/")
async def index():
    return FileResponse(CLIENT / "index.html")


@app.get("/analysis-client.mjs", include_in_schema=False)
async def analysis_client():
    return FileResponse(CLIENT / "analysis-client.mjs", media_type="text/javascript")


@app.get("/health")
async def health():
    return {
        "status": "ok", "service": "spatial-copilot", "version": app.version,
        "default_mode": "local", "data_source": "synthetic_demo",
    }


@app.get("/api/demo")
async def demo():
    return agent.sandbox.demo_layers()


@app.post("/api/query/stream")
async def stream_query(req: QueryRequest):
    async def event_generator():
        terminal = False
        try:
            async with aclosing(agent.run_stream(
                query=req.query, api_key=req.api_key, base_url=req.base_url, model=req.model,
                custom_point=req.custom_point.model_dump() if req.custom_point else None,
                mode=req.mode, distance_meters=req.distance_meters, min_capacity=req.min_capacity,
            )) as chunks:
                async for chunk in chunks:
                    if chunk["event"] in {"result", "error"}:
                        terminal = True
                    yield "data: " + json.dumps(chunk, ensure_ascii=False, allow_nan=False) + "\n\n"
                    if terminal:
                        return
            if not terminal:
                yield "data: " + json.dumps(agent.error("incomplete_stream", "分析未完成，事件流已结束。"), ensure_ascii=False) + "\n\n"
        except Exception:
            yield "data: " + json.dumps(agent.error("stream_failed", "结果输出失败，请重试。"), ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
