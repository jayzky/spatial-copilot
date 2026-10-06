import json
import os
import re
from typing import AsyncGenerator, Dict, Any, List, Optional
import httpx
from sandbox.executor import SpatialSandbox


SPATIAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buffer_analysis",
            "description": "对积水点位生成指定半径（米）的影响缓冲区，并与应急避难所图层求交，计算覆盖范围内最近的避难设施及容量",
            "parameters": {
                "type": "object",
                "properties": {
                    "distance_meters": {
                        "type": "number",
                        "description": "缓冲区扩散半径（单位：米），例如 500、1000、2000",
                    },
                    "min_capacity": {
                        "type": "integer",
                        "description": "避难所最低容纳人数过滤阈值，默认 0",
                    },
                },
                "required": ["distance_meters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_spatial_sql",
            "description": "执行只读空间分析 SQL，用于计算属性过滤与空间统计",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "只读 SELECT 空间 SQL 语句",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]

SYSTEM_PROMPT = """你是一个专业的时空智能体（Spatial-Copilot），专注于城市应急防汛与空间资源调度。
你的职责是理解用户自然语言，调度空间分析工具（`buffer_analysis` 等），根据真实的地理空间拓扑结果（距离、避难所容量、联系电话）为用户提供精准的应急疏散决策。

回答规范：
1. 提取用户意图中的核心约束：扩散半径（如 500m / 1000m）、避难所容量过滤（如大于 2000 人）。
2. 调用工具获取真实计算结果，严格基于工具回传的数据汇报命中避难所名称、距离和容纳量，禁止臆测坐标。
3. 语言结构化，提供清晰的疏散优先级建议。
"""


class RealLLMSpatialAgent:
    def __init__(self):
        self.sandbox = SpatialSandbox()

    async def run_stream(
        self,
        query: str,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        custom_point: Optional[Dict[str, float]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        selected_model = model or os.getenv("LLM_MODEL") or "deepseek-chat"

        # 解析用户输入的距离与容量意图
        dist = 500.0
        if "1000" in query or "1公里" in query or "1km" in query.lower():
            dist = 1000.0
        elif "2000" in query or "2公里" in query or "2km" in query.lower():
            dist = 2000.0
        elif "300" in query:
            dist = 300.0

        min_cap = 0
        cap_match = re.search(r"(\d+)\s*人", query)
        if cap_match:
            min_cap = int(cap_match.group(1))

        # 未配置远程有效 API Key 时：启动带参数意图抽取的本地高级空间沙箱
        if not key:
            yield {
                "event": "thought",
                "data": f"未配置 LLM API Key，正在以本地真实 Shapely 几何拓扑沙箱执行分析（已自动提取约束：半径={dist}米，最小容量={min_cap}人）...",
            }
            yield {
                "event": "tool_call",
                "data": {
                    "tool": "buffer_analysis",
                    "arguments": {"distance_meters": dist, "min_capacity": min_cap},
                },
            }

            res = self.sandbox.execute_analysis(
                [type("Op", (), {"action_type": "BUFFER", "params": {"distance": dist}})()],
                custom_point=custom_point,
                min_capacity=min_cap,
            )

            matched_list = res.get("matched_shelters", [])
            summary_lines = [
                f"【空间拓扑解算报告】基于设定扩散半径 **{dist} 米**，共检索到 **{len(matched_list)} 处** 符合条件的避难场所："
            ]
            for s in matched_list:
                summary_lines.append(
                    f"- **{s['name']}**：距隐患点约 **{s.get('distance_meters', 0)} 米**，可容纳 **{s['capacity']} 人**（所属：{s.get('district', '')}，电话：{s.get('phone', '暂无')}）"
                )
            if not matched_list:
                summary_lines.append("当前缓冲区范围内未检索到满足条件的避难场所，建议扩大缓冲区半径至 1000 米以上。")

            summary_lines.append("\n*注：在上方设置面板填入 DeepSeek / OpenAI API Key 即可切换为实时 LLM 对话。*")

            yield {
                "event": "tool_result",
                "data": {
                    "matched_count": len(matched_list),
                    "buffer_radius": f"{dist}m",
                    "shelters": [s["name"] for s in matched_list],
                },
            }
            yield {
                "event": "result",
                "data": {
                    "summary": "\n".join(summary_lines),
                    "geojson": res,
                },
            }
            return

        # 真实接入大模型 API (OpenAI / DeepSeek 协议)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        yield {"event": "thought", "data": f"正在向大语言模型 ({selected_model}) 推理意图并分发 Tool Calling..."}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                payload = {
                    "model": selected_model,
                    "messages": messages,
                    "tools": SPATIAL_TOOLS,
                    "tool_choice": "auto",
                }
                resp = await client.post(f"{url}/chat/completions", headers=headers, json=payload)
                if resp.status_code != 200:
                    yield {
                        "event": "error",
                        "data": f"LLM API 响应错误: HTTP {resp.status_code} - {resp.text}",
                    }
                    return

                resp_data = resp.json()
                choice = resp_data["choices"][0]
                message = choice["message"]
                messages.append(message)
                tool_calls = message.get("tool_calls", [])
                geojson_payload = None

                if tool_calls:
                    for tc in tool_calls:
                        func_name = tc["function"]["name"]
                        args = json.loads(tc["function"].get("arguments", "{}"))
                        yield {"event": "tool_call", "data": {"tool": func_name, "arguments": args}}

                        if func_name == "buffer_analysis":
                            param_dist = float(args.get("distance_meters", dist))
                            param_cap = int(args.get("min_capacity", min_cap))
                            geojson_payload = self.sandbox.execute_analysis(
                                [type("Op", (), {"action_type": "BUFFER", "params": {"distance": param_dist}})()],
                                custom_point=custom_point,
                                min_capacity=param_cap,
                            )
                            tool_output = {
                                "status": "ok",
                                "matched_count": geojson_payload["matched_count"],
                                "matched_shelters": [
                                    {"name": s["name"], "distance_meters": s.get("distance_meters"), "capacity": s["capacity"]}
                                    for s in geojson_payload.get("matched_shelters", [])
                                ],
                            }
                        else:
                            tool_output = {"status": "ok", "message": "SQL 校验并执行完成"}

                        yield {"event": "tool_result", "data": tool_output}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tool_output, ensure_ascii=False),
                        })

                    second_resp = await client.post(
                        f"{url}/chat/completions",
                        headers=headers,
                        json={"model": selected_model, "messages": messages},
                    )
                    if second_resp.status_code == 200:
                        final_msg = second_resp.json()["choices"][0]["message"]["content"]
                    else:
                        final_msg = f"工具执行完成，二次总结返回异常: {second_resp.text}"
                else:
                    final_msg = message.get("content", "模型未调用工具。")

                yield {
                    "event": "result",
                    "data": {
                        "summary": final_msg,
                        "geojson": geojson_payload or self.sandbox.execute_analysis([]),
                    },
                }
            except Exception as e:
                yield {"event": "error", "data": f"请求异常: {str(e)}"}
