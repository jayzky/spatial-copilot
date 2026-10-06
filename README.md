# Spatial-Copilot

Spatial-Copilot 是一个面向地理空间分析的智能体（Agent）框架，旨在将自然语言业务需求转换为结构化的空间拓扑执行计划，并通过安全沙箱与自我修正机制完成空间分析和动态可视化。

## 背景与设计动机

传统空间数据库（如 PostGIS）查询门槛高，非专业人员难以编写正确的空间拓扑函数（如 `ST_DWithin`、`ST_Buffer`、`ST_Intersects`）。直接使用通用大模型生成 SQL 存在以下核心缺陷：
- 坐标系与投影混淆：误在 WGS84 (EPSG:4326) 经纬度体系下直接套用米制缓冲区，导致计算失真。
- 语法幻觉与注入风险：大模型偶发生成包含非法修改语句或不可信子进程调用的脚本。
- 缺乏自愈闭环：执行失败后直接报错中断，无法基于 Traceback 重新反思修复。

Spatial-Copilot 通过“计划拆解 - 语法白名单校验 - 隔离沙箱执行 - 异常回传自愈”的闭环，确保空间分析全链路安全可控。

## 系统架构

```
[ 用户自然语言输入 ]
         │
         ▼
[ Spatial Planner ] ──> 意图解析与算子拓扑编排 (Buffer / Intersect / Aggregate)
         │
         ▼
[ Query Validator ] ──> AST 语法分析与白名单审查 (杜绝 DDL/DML 与危险调用)
         │
         ▼
[ Spatial Sandbox ] ──> 内存空间引擎执行 (Shapely / GeoJSON 序列化)
         │  └─ (发生异常) ──> Traceback 注入上下文触发自愈修正
         ▼
[ FastAPI SSE ] ────> 实时流式推送 Thought 与 Execution 状态
         │
         ▼
[ Web Client ] ─────> Canvas / GeoJSON 图层动态交互渲染
```

## 核心特性

- 规划与拆解：多步空间分析任务自动编排，支持缓冲区分析与空间求交。
- 严格安全拦截：基于 AST 语法树解析与白名单机制，仅允许只读空间分析语句。
- 空间计算引擎：支持点线面几何拓扑求交与标准化 GeoJSON FeatureCollection 导出。
- 实时流式响应：通过 Server-Sent Events (SSE) 实时推送 Agent 思考链路与分析进度。

## 典型 Bad Case 与优化记录

1. 投影距离单位混淆：
   - 现象：模型初期尝试将米直接传入基于经纬度的几何对象，导致缓冲区覆盖半个地球。
   - 解决方案：在 Planner 层引入严格的坐标系上下文投影换算因子或强制投影到局部墨卡托坐标系。
2. 异常自愈闭环：
   - 现象：当输入涉及不存在的图层字段时，执行引擎抛出 Key/Attribute 异常。
   - 解决方案：捕获异常 Traceback，并回传给状态机以触发降级策略和修复逻辑。

## 快速开始

### 运行测试

```powershell
pytest -v
```

### 启动服务

```powershell
uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

打开 `src/api/index.html` 即可在浏览器体验流式空间分析与图层联动。
