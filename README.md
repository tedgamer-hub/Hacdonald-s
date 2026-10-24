# Hacdonald's · 团队预算配餐 MVP

Hacdonald's 为黑客松和开发团队规划餐食：Skill 整理逐人需求，Python 生成预算方案，麦当劳官方 MCP 提供菜单和核价。现已具备餐品归一化、成员分配、三策略搜索及官方整篮核价流程。

**当前状态：65 项应用、传输及 SDK 测试通过，两轮真实团队配餐与官方核价已完成。** 2026-10-24 13:57（北京时间），用户终端在沈阳盛京大奥莱餐厅完成 50 个调用：3 人 80 元需求得到 64.00 / 78.50 / 79.00 元三套方案；成员 1 改为不要饮料、预算降为 75 元后，得到 64.00 / 73.50 / 73.50 元三套方案。各套均核对成员要求、商品数量和官方总价。项目内 Skill 已通过需求转换与连续修改试用，尚未安装到全局 Skill 目录。

这些金额是指定门店在该次核价时的结果；再次运行会查询当前价格。实际演示摘要见 `examples/demo-session.md`，完整业务记录只保存在本地。

## 快速开始

需要 Python 3.11+ 和 uv。以下命令在本项目目录运行。

```bash
uv sync
python3 scripts/run.py setup
uv run python scripts/run.py doctor
uv run python scripts/run.py tools
```

`setup` 会在你自己的交互终端中隐藏输入 Token，写入 `.local/credentials.json`。目录权限为 700，文件权限为 600，文件已被 `.gitignore` 排除。此文件以明文保存在本机，并非系统钥匙串；删除它即可移除本项目保存的凭证。不要将 Token 放进聊天、截图或命令参数。

也可以由外部进程注入 `MCD_MCP_TOKEN` 环境变量。环境变量优先；即使设为空字符串，也不会自动回退到文件。程序不自动读取 `.env`。

`scripts/run.py` 固定使用该源码项目的配置路径，支持从任意目录通过绝对路径运行。安装后的 `hacdonalds` 入口适用于本项目的 uv 可编辑安装；本版暂不支持脱离源码树单独分发 wheel 后的凭证管理。

`doctor` 不访问网络。`ready: true` 仅表示本地配置和依赖检查通过，不能作为真实 MCP 连接成功的证据。

依赖采用官方 SDK 2.x API，并包含 SOCKS 代理支持。安装完成后保留 uv 生成的 `uv.lock`，后续可用 `uv sync --locked` 复现依赖。**依赖锁文件已生成并包含 SOCKS 支持。**

## 查看真实参数，再执行查询

```bash
uv run python scripts/run.py schema stores
uv run python scripts/run.py schema menu
uv run python scripts/run.py schema detail
uv run python scripts/run.py schema quote
```

每个命令连接官方服务，读取 `tools/list` 中的实际 Schema。根据返回定义创建一个 UTF-8 JSON 参数文件，例如 `.local/stores-args.json`，再执行：

```bash
uv run python scripts/run.py stores --args-file .local/stores-args.json
uv run python scripts/run.py menu --args-file .local/menu-args.json
uv run python scripts/run.py detail --args-file .local/detail-args.json
uv run python scripts/run.py quote --args-file .local/quote-args.json
```

这些文件需要你根据真实 Schema 填写；仓库没有用假字段代替官方参数的示例。门店、商品 ID、套餐替换项应来自查询结果。不要凭餐名或标价构造最终核价，也不要把不同门店或过期查询中的商品混用。

每个单独命令会建立一个新会话；`plan` 的采集和方案核价在同一连接内执行。如需手工保持连接，使用交互模式：

```bash
uv run python scripts/run.py session
```

会话内支持 `tools`、`schema stores`、`schema menu`、`schema quote`，以及 `call <工具名或别名> <JSON对象>`。如 `call menu`，其后必须补充符合实际 Schema 的 JSON。`exit` 退出。

### 命令对应关系

| 命令 | 官方工具 |
|---|---|
| `stores` | `query-nearby-stores` |
| `menu` | `query-meals` |
| `detail` | `query-meal-detail` |
| `quote` | `calculate-price` |
| `coupons` | `query-store-coupons` |
| `nutrition` | `list-nutrition-foods` |

也支持 `call <官方工具名> --args-file ...`。只有上述六种工具可执行；`tools` 会展示服务器发现的其他工具并标为 `enabled: false`。

`mcp-config.example.json` 是参考用的客户端配置。各客户端的传输类型字段和 `${MCD_MCP_TOKEN}` 展开机制可能不同，需要按目标客户端说明调整。它不会注册本 Python 项目的 CLI，也不会自动配置本聊天的 MCP 工具。

## 返回值与异常

成功调用返回 `source`、`tool`、`retrieved_at`、`encoding`、`data` 和 `content`。`source: live` 表示来自当前工具调用；不是订单支付成功或上游业务成功的额外保证。

- 优先使用 `structured_content`，缺失时尝试单个 JSON 文本，否则保留文本。
- 参数按发现的 Schema 校验，不满足要求时不调用业务工具。
- 查询命令保留上游业务返回结构。`plan` 另行验证业务成功、商品身份及数量，将详情和核价归一化。即使 MCP 层未报错，也不代表业务成功。
- 401/403、429、超时均给出独立错误码；应用层不自动重试业务调用。SDK 自身的协议恢复行为以其文档为准。
- Token 在输出中脱敏，不自动保存业务响应。终端仍可能展示你查询的位置和个人优惠信息，请勿直接将完整输出公开。
- 若使用 shell 重定向保存响应，放到 `.local/` 下；公开 Fixture 需另行审查和脱敏。

## 团队预算配餐

编辑 `examples/team-request.json`，将门店换成实际查询并选定的编码，再运行：

```bash
uv run python scripts/run.py plan --request examples/team-request.json --output .local/team-plan.json
```

示例为 3 人总预算 80 元：成员 1 偏好鸡肉名称，成员 2 不要饮品，成员 3 食量为 4。金额采用整数分。每位成员至少一份主食；食量 4–5 再要求一份小食。`drink` 可为 `optional`、`required`、`none`。

流程依次读取官方菜单、详情和单品参考核价，进行确定性的有界搜索，再核验整个商品篮子。默认采集上限 24，`--limit 32` 可适度扩大；最多查询 9 个不同候选篮子，重复篮子在本次运行内复用核价。`--output` 使用权限 600 的文件保存结果。首次采集可能需要一两分钟，进度写入 stderr，结果 JSON 写入 stdout。

三个策略分别是价格优先 `saver`、均衡搭配 `balanced`、丰富搭配 `full_power`。仅返回官方价格不超预算且明细商品、数量匹配的方案；不足三套时不复制凑数。`reference_total_fen` 是搜索参考，最终展示只用 `official_quote.amount_fen`。未取得已验证方案时退出码为 2。

搜索只覆盖本次采集的单品：套餐 `rounds` 非空时跳过，酱料和餐具不计作小食，不保证全菜单全局最优。每名成员最多保留 64 个候选组合，每层最多保留 512 个搜索状态，不引入额外求解器依赖。食量规则不代表热量或饱腹保证。

`prefer` 是名称关键词软偏好；`exclude_keywords` 仅在用户明确要求按名称排除时使用。配料、过敏原及“不吃牛肉/辣”等要求保留在 `avoid`；当前无法核实，非空时返回 `needs_dietary_verification`，不会以“名字没出现”代替配料验证。尚未支持套餐组合、特调、优惠券优化、营养目标或分账。

## 使用项目 Skill

Skill 位于 `skills/hacdonalds/`，包含工作流、请求协议、UI 元数据和源码入口桥接。让支持读取 Skill 的客户端显式使用该目录，即可把自然语言转为需求 JSON 并运行 `plan`。本版未更改客户端全局设置，自动发现目录因客户端而异。

连续修改时保留原请求的门店和其他成员，只更新明确修改的条件，再运行一次核价。需求解析由宿主模型完成；本项目不额外调用另一个模型 API。

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

也可在已有 `jsonschema` 的系统 Python 下运行 `python3 -m unittest discover -s tests -v`。测试中的 `test_store_id`、`test_total_fen` 等字段只属于测试假数据。

测试覆盖配置优先级、文件权限、隐藏输入边界、分页、Schema、工具白名单、结构化/文本返回、错误与超时不重试。`test_sdk_protocol.py` 在安装 SDK 2.x 后执行内存协议集成；SDK 不存在时明确跳过。

真实验收需要逐项记录：

1. Token 配置后 `tools` 成功列出工具。
2. 查看 Schema 后查询一个指定地点的门店。
3. 对同一门店查询可售菜单和一个餐品的详情。
4. 在同一业务上下文中核价一个简单组合，检查上游业务状态和最终金额。

本版不执行订单创建、取消、支付、地址新增、领券或积分兑换。套餐、优惠券和营养优化尚未接入；本地参赛材料不代表已发布仓库或已报名。

## 一条命令验收两轮配餐

```bash
uv run python scripts/verify_team.py
```

脚本重新查询当前门店，验证示例中的 3 人 80 元需求，再将成员 1 改为不要饮料、预算改为 75 元，进行新一轮官方核价。第二轮复用刚采集的搜索参考，整篮价格重新查询。不会创建订单或自动更换门店。

每次调用前后写入 `.local/team-check.json`，失败也保留已完成阶段。退出码 0 表示两轮均获得已核价方案；2 表示未完成。完整记录仅保存在本地，不应直接公开。连接失败时不自动重试，也不把旧金额说成实时金额。

默认每次请求超时为 30 秒，可指定 `uv run python scripts/verify_team.py --timeout 45`（范围 1–120）。记录包含每个调用耗时、异常类型、HTTP/RPC 错误码和脱敏 RPC 消息；请求因会话关闭而中断时标记为 interrupted，不再残留 pending。RPC 超时代码 -32001 显示为 TIMEOUT，HTTP 认证、限流和服务器错误分别识别，避免全部显示成 MCP_FAILED。

## 官方来源

- [麦当劳 MCP 接入说明](https://github.com/M-China/mcd-mcp-server)
- [麦当劳 MCP 平台](https://open.mcd.cn/mcp)
- [MCP Python SDK 2.x 客户端](https://py.sdk.modelcontextprotocol.io/client/)
- [SDK HTTP 传输及认证配置](https://py.sdk.modelcontextprotocol.io/client/transports/)

Hacdonald's 是独立开发项目，非麦当劳官方产品。

## 一次完成菜单、详情和核价验证

在已保存真实 `.local/stores-live.json` 后运行：

```bash
uv run python scripts/verify_live.py
```

脚本从保存的门店结果选第一家营业餐厅，在同一 MCP 会话内查询当前菜单，最多尝试 5 个来自菜单的商品编码，查询详情并核价一份默认商品。它是技术连通验证，不是多人配餐推荐。已适配真实菜单的 `categories[].meals[]` 结构：名称缺失时保留编码，优先选择带“单品”标签的商品；需要显式套餐搭配或返回结构未知时会停止并留下记录，不编造名称、配菜或金额。

2026-10-22 的首次验证中，菜单查询成功，本地脚本因商品无名称而停止。该解析问题已修正，同时修复 SDK 异常组掩盖业务错误的问题，失败时会显示具体错误码和说明。

每个阶段保存到 `.local/live-check.json`，包括未完成时的错误和已取得的原始响应。成功仅表示已收到可识别的业务成功结果；金额字段仍需结合实际返回核对。该记录仅保存在本机，不应直接公开。

本机可编辑安装的路径文件曾被标记为隐藏，Python 3.13 会跳过该文件，导致 `hacdonalds` 控制台入口报模块不存在。本文统一使用 `uv run python scripts/run.py ...` 的源码入口，避免依赖这个路径文件。
