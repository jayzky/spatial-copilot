# Spatial-Copilot

自然语言空间分析演示：解析半径与最低容量，执行预定义空间计算，并通过 SSE 与 Leaflet 展示过程和结果。

## 已实现

- 本地规则模式无需密钥，支持数字米/公里和最低容量；严格大于容量会转换为整数下界。容量上界等当前不支持的条件返回错误。
- 远程模式使用 OpenAI 兼容接口完成一次工具调用和二次总结。仅暴露 `buffer_analysis`，不执行 SQL、任意 Python 或未注册工具。
- 使用 WGS84 椭球和以每个分析点为中心的局部等距方位投影（AEQD），以米计算设施距离。按最小距离筛选并排序，多个中心不会重复返回同一设施。
- 缓冲可视化采用每象限 128 段的离散多边形，返回经纬度 GeoJSON；候选判定采用局部投影的米制距离，包含半径边界。
- 半径、容量、坐标与模型参数均校验；结果仅包含 JSON 类型。HTTP 验证失败返回 422，流内失败以结构化 `error` 结束。
- 同源页面、地图、属性表与 GeoJSON 导出；后端失败时页面明确显示失败，不切换成浏览器内计算。
- 所有默认点位、容量和水深均为自建演示数据，不代表实时监测、实际设施服务状态或真实联系电话。

## 运行

本机已有 Python 环境可直接运行，无需本次安装依赖：

```powershell
Set-Location -LiteralPath 'C:/Users/29642/Desktop/resume/projects/spatial-copilot'
& 'D:/anaconda/python.exe' -m uvicorn --app-dir src api.server:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`。页面使用本地 Leaflet，底图瓦片和可选图标依赖网络；底图不可用时仍可计算和显示演示要素。通过服务地址访问页面，不直接打开 HTML 文件。

- 输入示例：`750米范围内，至少3000人`、`1.5公里，超过2000人`。
- 在设置中显式选择本地或远程模式。远程模式的密钥仅用于请求，不写入页面存储。
- 如使用服务器环境密钥，先在本地环境配置 `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY`，并配置对应 `OPENAI_BASE_URL`、可选 `LLM_MODEL`。服务端密钥只能发送到服务器配置地址；客户端自定义模型地址必须同时提供自己的密钥。
- 当前默认模型地址为 `https://api.deepseek.com`；其他兼容服务须设置实际地址。远程调用会产生对应服务的请求，测试不会读取真实密钥或发出付费调用。

运行时依赖在 `pyproject.toml` 声明。本机同时已有启动服务用的 uvicorn 和测试用的 pytest。

## 接口

- `GET /`：交互页面。
- `GET /api/demo`：自建图层与数据来源说明。
- `POST /api/query/stream`：SSE 分析。字段含 `query`、`mode`（默认 local）、可选 `distance_meters`、`min_capacity`、`custom_point`（lon/lat），以及远程模型配置。
- 半径：有限正数且不超过 50,000 米；最低容量：0–10,000,000 的整数。
- 自定义点限于东经 113.5–115.5、北纬 29.5–31.5 的武汉周边演示区域。
- 事件为 `mode → thought → tool_call → tool_result → result`；其中 thought 仅为程序执行状态。任何阶段都可能以 `error` 结束，之后不会继续发送成功结果。

## 验证

```powershell
& 'D:/anaconda/python.exe' -m pytest -q -p no:cacheprovider
```

测试覆盖 JSON 序列化、Geod 构造的半径内/边界/半径外点、重复中心、无命中、参数边界、完整本地 SSE、未知工具、异常模型响应、上下游 HTTP 失败与超时、服务端密钥的目标地址限制。远程分支使用 HTTPX MockTransport，属于离线协议验证，不等同于真实模型服务联调。

当前不含 PostGIS、通用 SQL 执行、隔离脚本沙箱、自动修复、真实设施数据或生产部署能力。
