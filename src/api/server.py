import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent.workflow import SpatialCopilotWorkflow

app = FastAPI(title="Spatial-Copilot API")
workflow = SpatialCopilotWorkflow()


class QueryRequest(BaseModel):
    query: str


@app.post("/api/query/stream")
async def stream_query(req: QueryRequest):
    def event_generator():
        result = workflow.run(req.query)
        for node in result["trace"]:
            yield f"data: {json.dumps({'event': 'trace', 'data': node}, ensure_ascii=False)}\n\n"
        final_payload = {
            "event": "result",
            "data": {
                "summary": result["summary"],
                "geojson": result["geojson"],
            },
        }
        yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
