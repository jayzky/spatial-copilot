import json
import os
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from agent.planner import SpatialOperation, parse_parameters
from agent.validator import AnalysisParameters, AnalysisPoint
from sandbox.executor import SpatialSandbox


SPATIAL_TOOLS = [{
    "type": "function",
    "function": {
        "name": "buffer_analysis",
        "description": "在自建演示数据中按半径和最低容量筛选设施，返回米制距离与地图数据",
        "parameters": {
            "type": "object",
            "properties": {
                "distance_meters": {"type": "number", "exclusiveMinimum": 0, "maximum": 50000},
                "min_capacity": {"type": "integer", "minimum": 0, "maximum": 10000000},
            },
            "required": ["distance_meters"],
            "additionalProperties": False,
        },
    },
}]
SYSTEM_PROMPT = """你是空间分析演示助手。数据为自建样例，不是实时监测或真实应急服务。
从用户输入提取半径（米）和最低容量，必须调用 buffer_analysis 获取结果。
严格依据工具返回的设施、距离和容量解释筛选结果，不补造设施、电话或服务状态。
将筛选过程和演示限制简洁说明，不提供实际应急调度指令。"""


class RealLLMSpatialAgent:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.sandbox = SpatialSandbox()
        self.transport = transport

    @staticmethod
    def error(code: str, message: str) -> Dict[str, Any]:
        return {"event": "error", "data": {"code": code, "message": message}}

    @staticmethod
    def response_message(response: httpx.Response) -> Dict[str, Any]:
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        if not isinstance(message, dict):
            raise ValueError("模型消息格式无效")
        return message

    def calculate(self, parameters: AnalysisParameters, custom_point):
        operation = SpatialOperation(
            step=1, action_type="BUFFER", target_layer="waterlog_points",
            params={"distance": parameters.distance_meters},
        )
        return self.sandbox.execute_analysis(
            [operation], custom_point=custom_point, min_capacity=parameters.min_capacity
        )

    @staticmethod
    def summary(result: Dict[str, Any]) -> str:
        parameters = result["parameters"]
        lines = [
            f"演示分析：半径 {parameters['distance_meters']:g} 米，"
            f"最低容量 {parameters['min_capacity']} 人，命中 {result['matched_count']} 处设施。"
        ]
        lines.extend(
            f"- {s['name']}：距离 {s['distance_meters']:.1f} 米，容量 {s['capacity']} 人。"
            for s in result["matched_shelters"]
        )
        if not result["matched_shelters"]:
            lines.append("当前范围和容量条件下没有匹配设施。")
        lines.append("数据为自建样例，不代表实时积水、设施容量或开放状态。")
        return "\n".join(lines)

    async def run_stream(
        self, query: str, api_key: str = "", base_url: str = "", model: str = "",
        custom_point: Optional[Dict[str, float]] = None, mode: str = "auto",
        distance_meters: Optional[float] = None, min_capacity: Optional[int] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            if not isinstance(query, str) or not query.strip() or len(query) > 4000:
                raise ValueError("查询须为1–4000字的非空文本")
            if mode not in {"auto", "local", "remote"}:
                raise ValueError("运行模式无效")
            parameters = parse_parameters(query)
            parameters = AnalysisParameters.model_validate({
                "distance_meters": parameters.distance_meters if distance_meters is None else distance_meters,
                "min_capacity": parameters.min_capacity if min_capacity is None else min_capacity,
            })
            if custom_point is not None:
                custom_point = AnalysisPoint.model_validate(custom_point).model_dump()
        except ValidationError as exc:
            message = exc.errors(include_input=False, include_context=False)[0]["msg"]
            yield self.error("invalid_parameters", "分析参数无效：" + message)
            return
        except ValueError as exc:
            yield self.error("invalid_parameters", str(exc))
            return
        except TypeError:
            yield self.error("invalid_parameters", "查询、半径、容量或坐标无效；半径须在0–50000米内，容量为非负整数，点位须在演示区域。")
            return

        key = "" if mode == "local" else (
            api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        )
        selected_mode = "remote" if mode == "remote" or (mode == "auto" and key) else "local"
        yield {"event": "mode", "data": {"mode": selected_mode, "data_source": "synthetic_demo"}}
        if selected_mode == "remote" and not key:
            yield self.error("missing_api_key", "远程模型模式需要配置密钥；可手动选择本地规则模式。")
            return
        if selected_mode == "local":
            yield {"event": "thought", "data": "本地规则模式：解析分析条件并执行米制空间计算。"}
            yield {"event": "tool_call", "data": {"tool": "buffer_analysis", "arguments": parameters.model_dump()}}
            try:
                result = self.calculate(parameters, custom_point)
            except Exception:
                yield self.error("analysis_failed", "空间计算失败，请检查数据与分析参数。")
                return
            yield {"event": "tool_result", "data": {
                "matched_count": result["matched_count"], "parameters": result["parameters"],
                "distance_method": result["distance_method"],
            }}
            yield {"event": "result", "data": {
                "summary": self.summary(result), "geojson": result, "mode": "local",
            }}
            return

        configured_url = (os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        url = (base_url or configured_url).rstrip("/")
        if not api_key and url != configured_url:
            yield self.error("server_key_endpoint_mismatch", "自定义模型地址须提供自己的密钥；服务器密钥仅用于服务器配置的地址。")
            return
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            yield self.error("invalid_base_url", "模型地址须为不含用户名、密码、查询参数的 HTTP(S) 地址。")
            return
        selected_model = model or os.getenv("LLM_MODEL") or "deepseek-chat"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query + "\n当前结构化条件：" + json.dumps(parameters.model_dump(), ensure_ascii=False)},
        ]
        yield {"event": "thought", "data": "远程模型模式：请求工具参数，使用本地样例数据计算后回传结果。"}
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=self.transport, trust_env=False
            ) as client:
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                response = await client.post(f"{url}/chat/completions", headers=headers, json={
                    "model": selected_model, "messages": messages, "tools": SPATIAL_TOOLS,
                    "tool_choice": {"type": "function", "function": {"name": "buffer_analysis"}},
                })
                message = self.response_message(response)
                calls = message.get("tool_calls")
                if not isinstance(calls, list) or len(calls) != 1:
                    yield self.error("missing_tool_call", "模型须返回且仅返回一次空间分析工具调用。")
                    return
                call = calls[0]
                if call["function"]["name"] != "buffer_analysis":
                    yield self.error("unsupported_tool", "模型请求了未实现的工具，未执行。")
                    return
                call_id = call["id"]
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("缺少工具调用标识")
                arguments = json.loads(call["function"]["arguments"])
                if not isinstance(arguments, dict) or "distance_meters" not in arguments:
                    raise ValueError("工具调用缺少必填半径")
                validated = AnalysisParameters.model_validate(arguments)
                if distance_meters is not None or min_capacity is not None:
                    validated = AnalysisParameters(
                        distance_meters=validated.distance_meters if distance_meters is None else distance_meters,
                        min_capacity=validated.min_capacity if min_capacity is None else min_capacity,
                    )
                yield {"event": "tool_call", "data": {"tool": "buffer_analysis", "arguments": validated.model_dump()}}
                result = self.calculate(validated, custom_point)
                tool_output = {
                    "data_source": result["data_source"], "parameters": result["parameters"],
                    "matched_count": result["matched_count"],
                    "matched_shelters": [
                        {"name": s["name"], "distance_meters": s["distance_meters"], "capacity": s["capacity"]}
                        for s in result["matched_shelters"]
                    ],
                }
                yield {"event": "tool_result", "data": tool_output}
                messages.extend([
                    {"role": "assistant", "content": message.get("content"), "tool_calls": calls},
                    {"role": "tool", "tool_call_id": call_id, "content": json.dumps(tool_output, ensure_ascii=False, allow_nan=False)},
                ])
                response = await client.post(f"{url}/chat/completions", headers=headers, json={
                    "model": selected_model, "messages": messages,
                })
                final = self.response_message(response)
                summary = final.get("content")
                if not isinstance(summary, str) or not summary.strip() or final.get("tool_calls"):
                    raise ValueError("模型总结无效")
                yield {"event": "result", "data": {
                    "summary": summary + "\n\n自建演示数据，不代表实时监测或实际设施服务状态。",
                    "geojson": result, "mode": "remote",
                }}
        except httpx.TimeoutException:
            yield self.error("upstream_timeout", "模型服务超时，请重试或手动选择本地规则模式。")
        except httpx.HTTPStatusError as exc:
            yield self.error("upstream_http", f"模型服务返回 HTTP {exc.response.status_code}，分析未完成。")
        except httpx.RequestError:
            yield self.error("upstream_network", "无法连接模型服务，请检查地址与网络。")
        except (ValueError, TypeError, KeyError, IndexError):
            yield self.error("invalid_model_response", "模型响应结构、工具参数或总结无效，分析未完成。")
        except Exception:
            yield self.error("analysis_failed", "工具执行失败，分析未完成。")
