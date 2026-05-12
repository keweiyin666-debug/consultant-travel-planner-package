# 顾问出差行程安排技能包

## 目录结构
- skill/ - 技能定义（SKILL.md、脚本、参考文档、模板）
- skill/references/travel_policy.md - 公司差旅硬性政策
- skill/references/user_preferences.md - 用户长期偏好和实测经验
- skill/references/routing_rules.md - 跨城交通路由与排序规则
- skill/references/flight_source_issues.md - 航班数据源已知问题、漏查复盘和强制核验规范
- mcp_servers/ - MCP服务配置（航班查询、高铁查询、高德地图）
- travel-planner.html - Web表单页面
- 行程单示例.md - 示例行程单

## 本地运行配置

本项目按本地运行设计，不依赖 Corevo 连接中心。核心配置分三类：

1. 高德地图：使用项目本地配置文件保存高德 Web Service Key，后续调用高德 MCP 时自动读取。
2. 高铁：通过 `mcp_servers/12306-mcp/mcp.json` 调用 `12306-mcp` 查询车次、票价和余票。
3. 航班：通过 `mcp_servers/flight-ticket-server/mcp.json` 调用 FlyClaw 聚合航班后端；该配置保留原 MCP 名称，但实际入口为 `mcp_servers/flyclaw-flight-server/run_flyclaw_mcp.py`。

Codex 沙盒注意事项：当前 Codex shell 可能带有 `CODEX_SANDBOX_NETWORK_DISABLED=1`，这种环境下 Python 的 DNS/外网请求会失败，FlyClaw 诊断中会出现 `NameResolutionError` 或 `Could not resolve host`。这不是航班源无数据。遇到该情况，应将同一 `.venv-flight/bin/python ... FlyClaw` 查询在沙盒外重试；沙盒外查询可正常访问飞猪 MCP，并应优先作为航班数据来源。

本地依赖安装：

```bash
pip install requests pyyaml curl_cffi flights cryptography
npm install -g 12306-mcp @amap/amap-maps-mcp-server
```

高德 Key 配置方式：

```bash
cp config/amap.env.example config/amap.env
# 编辑 config/amap.env：
# AMAP_WEBSERVICE_KEY=你的高德Web服务Key
```

`config/amap.env` 已加入 `.gitignore`，用于本机保存真实 Key。`mcp_servers/amap-maps/run_amap_mcp.sh` 会在启动高德 MCP 时自动读取 `config/amap.env` 或项目根目录 `.env`。

## 差旅政策
- 飞机：经济舱 | 高铁：二等座 | 酒店：汉庭大床房
- 航班选择：直飞 > 联程 > 临近城市+高铁接驳

## 用户偏好
- 航空公司：优先选择东方航空（MU）或上海航空（FM）。
- 无锡出发：若无锡本地没有合适的东航/上航航班，可接受从无锡东乘高铁到上海虹桥，再从上海虹桥机场转飞机。
- 虹桥换乘经验：上海虹桥高铁站出站口到虹桥机场 T2 约 15 分钟，T2 安检约 20 分钟；基础换乘耗时按 35 分钟估算，并根据值机、托运和节假日增加缓冲。
- 已知航班源问题：旧版 FlyClaw 本地包装可能把精确机场 `SHA` 降级为“上海”城市级查询，导致虹桥出发/返回航班被浦东结果覆盖；飞猪源本身可用 `SHA` 或“上海虹桥”查到虹桥航班。无锡出发及虹桥高频行程必须按 `skill/references/flight_source_issues.md` 保留 `SHA` 精确机场约束，不能把浦东 PVG 结果当作虹桥方案。

## 架构说明

当前技能包按“入口说明 -> 规则知识 -> 工具查询 -> 行程渲染”分层：

1. `skill/SKILL.md` 定义执行流程、输入要求和交付格式。
2. `skill/references/` 存放可维护规则：
   - `travel_policy.md`：公司政策，作为硬约束。
   - `user_preferences.md`：个人偏好和实测经验，作为软约束。
   - `routing_rules.md`：交通方案排序、过滤和接驳规则。
   - `flight_source_issues.md`：航班数据源漏查案例和补充核验规范。
   - `checklist_templates.md`：出差清单模板。
3. `mcp_servers/` 负责实时数据来源，包括航班、高铁和地图；航班由 vendored FlyClaw 聚合数据源提供，高德地图通过项目本地 `config/amap.env` 读取 `AMAP_WEBSERVICE_KEY`。
4. `travel-planner.html` 是本地表单页面，只负责收集行程信息并提交到本地规划接口。
5. `skill/scripts/itinerary_planner.py` 负责在已查询到数据后生成 Markdown 行程单草案。

后续建议把个人偏好继续沉淀在 `user_preferences.md`，不要写进 `travel_policy.md`；这样公司政策、个人偏好和实时数据三类信息边界清晰，后续多人使用时也更容易扩展成不同用户画像。

## 本地脚本

可用脚本生成行程单草案：

```bash
python3 skill/scripts/itinerary_planner.py --input trip.json --output itinerary.md
```

不传 `--input` 时会输出内置示例，便于验证安装是否正常。
