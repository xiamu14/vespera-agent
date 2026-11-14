# Ani Bot 网络与数据外发 Spec

**网络外发按「实际数据 × 实际目的地」自动判定。** 通过即发送，命中隐私规则即拒绝。网络隐私 Hook 只返回 `allow / deny`，无需弹窗。文件写入、远端修改等操作权限继续按 [权限 Spec](permissions.md) 判定。

实现状态见文末。清单覆盖当前应用网络入口和拟接入的命令族。表外命令仍进入统一未知协议规则，新增工具须登记实际出口。

## 1. 工具登记与检查入口

共享的 Pydantic AI `before_tool_execute` 是网络工具的检查入口。工具注册时声明网络分类与准备函数。Shell 按实际命令分类，动态 MCP 工具按注册时绑定的服务分类。

| 工具 | 登记方式 | 对应规则 |
|---|---|---|
| `shell` 及启用的 `run_command / start_command` | 命令分类器区分本地执行和联网操作 | NET-01–08、NET-13–16 |
| `web_fetch` | HTTP 读取 | NET-01 |
| `web_search` | 搜索服务请求 | NET-05 |
| 动态注册的 MCP 工具 | 使用服务 ID 与传输类型。HTTP 使用固定服务地址，stdio 使用受控网络能力 | NET-09 |
| 后续注册的上传、发布、通知等工具 | 注册时绑定对应适配器 | NET-02、NET-07、NET-08、NET-14、NET-15 |
| 文件、记忆、计划、交接、Git 本地读写 | 声明为本地操作，跳过网络检查 | 后续产生的外发按对应规则检查 |

```python
async def before_tool_execute(ctx, *, call, tool_def, args):
    operation_policy.check(ctx, call, tool_def, args)
    operation = network_tools.classify(tool_def, args)
    if operation.is_local:
        return args

    request = operation.adapter.prepare(ctx, args)  # 固定载荷与目的地
    grant = privacy_hook.check(request)
    if grant.denied:
        raise SkipToolExecution(grant.reason)
    bind_to_current_call(ctx.run_id, call.tool_call_id, request, grant)
    return args
```

| 对象 | 必须携带 |
|---|---|
| 外发请求 | 操作、协议、实际 origin/账户/仓库、方法、路径、查询、可发送头、实际业务载荷、数据来源与类别、预期副作用 |
| 授权 | 调用 ID、目的地、载荷或对象集摘要、操作范围、策略版本、请求生命周期 |
| 分类结果 | 类别、规则 ID、置信度、脱敏位置或原因 |

- 执行器消费当前调用绑定的请求。实际目的地和载荷必须与检查时一致。重试、重定向和 Git 协商后的新增外发阶段复用同一个判定函数。
- Shell 脚本或 stdio 进程无法声明实际目的地和载荷时，其直连由隔离层阻断。传输层执行 TLS、DNS/IP、认证绑定和目标校验。
- 模型请求、MCP 初始化与发现、Jev、遥测和后台更新各自从应用内部发起。在各自调用前直接复用同一判定函数。Jev 自身按 §4 做本地前置检查。
- 复用 [现有工具权限 Hook](../agent_core/agent/permissions/policy.py)。网络隐私拒绝直接结束调用；操作权限仍按 [权限 Spec](permissions.md) 判定。

## 2. 操作与命令清单

`allow` 条件统一为载荷检查完整、目的地允许、无阻断命中且执行端可落实授权。缺少任何条件即 `deny`。

| ID | 操作或命令族 | 检查与接入 |
|---|---|---|
| NET-01 | HTTP 读取。WebFetch、`curl`、`wget`、`http`、`xh` 的 GET/HEAD | 检查 URL、路径、查询、请求头及 Cookie。逐跳重查重定向。响应写入本地文件走文件权限。 |
| NET-02 | HTTP 提交、上传和修改。上述客户端的 POST/PUT/PATCH/DELETE，`curl -d`、`-F`、`-T`、`@file`、stdin，REST/GraphQL、Webhook | 解析 JSON、表单、multipart 和明确的文件载荷。固定最终请求字节；目的地变化时重绑认证。远端变更走操作权限。无法固定的流式上传拒绝。 |
| NET-03 | Git 远端查询和下载。`ls-remote`、`clone`、`fetch`、`pull`、`remote show`、远端 submodule 更新、LFS fetch/pull | 检查实际远端、认证范围、引用请求及本地副作用。递归 submodule/LFS 每个目的地单独校验。下载内容写入、合并及覆盖走本地操作规则。 |
| NET-04 | Git 推送。`git push` 的分支、标签、删除引用、强推、批量 refspec、LFS push | 固定仓库、远端、源对象和目标引用。协商后检查将外发的对象与元数据。删除、强推、批量修改适用各自操作规则。未受控的 Git helper、LFS 上传和其他协议拒绝。 |
| NET-05 | 网页搜索。当前 Tavily `web_search`、未来其他搜索服务 | 查询、过滤条件和请求元数据按外发文本处理。认证由搜索适配器绑定。 |
| NET-06 | 包、依赖及镜像下载。`pip/uv/poetry`、`npm/pnpm/yarn/bun`、`cargo`、`brew`、`go`、`docker/podman pull`、系统包管理器 | 检查实际 registry、私有包名、版本、查询和认证。每个下载、镜像、重定向都检查。构建及安装脚本需受同一出口约束。 |
| NET-07 | 发布与部署。`npm publish`、`twine upload`、`cargo publish`、`docker/podman push`、`gh/glab` 发布或上传、云 CLI 部署 | 固定上传包、镜像层、构建产物、元数据和目的地。远端状态改变走操作权限；无法取得完整载荷时拒绝。 |
| NET-08 | 远端文件传输及命令。`ssh`、`scp`、`sftp`、`rsync`、FTP、云存储 CLI、远端容器或集群执行 | 检查目标账户、路径、发送文件及远端命令。当前范围内无可解析适配器的协议或交互会话拒绝。 |
| NET-09 | MCP。HTTP 初始化、工具发现、工具调用、资源读取、通知；本地 stdio MCP 子进程的网络访问 | 工具调用由 `before_tool_execute` 检查请求参数和服务 origin。HTTP 初始化与发现由连接入口检查。stdio 可启动后，经受控出口外发。模型工具参数审批无法覆盖子进程的自行联网。 |
| NET-10 | 模型服务。Super、角色 Agent、分身、模型重试或切换 | 在实际模型请求前检查最终消息、指令、附件、历史、工具结果和目标模型服务。模型和 endpoint 取生效配置。 |
| NET-11 | Jev 分类请求 | 独立 OpenRouter Decisions 客户端。只发通过 §4 本地审查的最小特征或可外发内容。 |
| NET-12 | 遥测、日志和错误报告。CLI Logfire、未来崩溃上报 | 发送前投影为允许的指标字段。请求正文、附件、命令参数、配置和凭据禁止进入默认遥测。 |
| NET-13 | DNS 与连通性。`dig`、`nslookup`、`host`、`ping`、`traceroute`、`nc`、`telnet`、`openssl s_client` | DNS 名、TLS 目标和探测参数也属于外发数据。应用 DNS 随已审查目的地执行；探测经专用适配器，原始 socket 和不可检查的协议拒绝。 |
| NET-14 | 浏览器和外部应用链接、OAuth 登录、表单和文件上传 | Ani Bot 发起时检查实际 URL 和交付数据。打开链接与后续用户自主浏览各自有边界；自动表单及上传必须接入受控出口。 |
| NET-15 | 聊天、邮件、通知、Issue/PR、工单与各类远端 API 写入 | 检查实际接收方、正文、附件和服务。按远端副作用规则执行。MCP、HTTP 和 CLI 入口共享同一隐私规则。 |
| NET-16 | 任意项目脚本或通用客户端。`python/node/ruby`、shell 脚本、测试、构建、依赖生命周期、插件、MCP helper、代理、隧道、WebSocket、gRPC | 能经受控适配器逐请求或逐消息检查时按对应规则处理。无法识别目的地或完整载荷时拒绝网络操作。 |
| NET-17 | 自动更新、资源同步、应用后台下载 | 当前作为未来入口登记。启用前配置真实下载来源，检查出站查询、遥测及认证，下载侧验证来源和内容完整性。 |

本地文件与进程、Git 状态/差异/暂存/提交、会话存储、附件读取、记忆和本地 Hook 在本地处理时无需发起网络判定。其产物随后发给模型、MCP 或其他服务时，在对应出口检查。

当前源代码入口包括 [MCP](../agent_core/integrations/mcp.py)、[HTTP 传输](../agent_core/integrations/network.py)、[网页搜索](../agent_core/tools/web_tools.py)、[模型配置](../agent_core/integrations/provider.py)、[Ani Bot 运行时](../agent_core/vespera_agent/runtime.py) 和 [CLI 遥测](../agent_core/agent/runtime.py)。

## 3. 隐私判定

### 内置规则

工具登记决定使用哪组代码规则。规则在共享网络判定器中定义，所有角色和调用入口复用。

| 工具 | 本地直接放行 | 本地直接拒绝 |
|---|---|---|
| `web_fetch` | 允许的公网 HTTPS 目的地，GET/HEAD，无请求体，自定义参数和请求头通过本地扫描 | 禁止地址、URL 内嵌认证、凭据或敏感数据命中、超出支持的请求形态 |
| `web_search` | 配置的搜索服务，查询和过滤字段通过本地扫描，内容属于该服务允许接收的类别 | 查询携带已知令牌、私钥、受保护文件内容，或目标服务与配置不符 |
| MCP | 工具及服务已登记，实际服务地址、参数结构和数据类别均符合该工具的策略，本地扫描通过 | 服务或工具未登记、目的地不符、参数无法完整检查，或命中该服务禁止的数据类别 |

```python
result = evaluate_builtin_rules(request)  # allow / deny / needs_classification
if result == "needs_classification":
    result = evaluate_with_jev(request)   # §4 检查分类请求本身
return result                            # 对外只返回 allow / deny
```

仅当已知目的地和可检查载荷符合基本条件，且规则需要识别正文中的隐私类别时，才进入 Jev。未命中放行规则的请求按默认拒绝处理。Jev 的结果不能覆盖内置拒绝。

| 顺序 | 条件 | 结果 |
|---|---|---|
| PRIV-01 | 目标不允许、元数据地址、未受控出口、认证目标不匹配 | `deny` |
| PRIV-02 | 系统内部数据、凭据、私钥、已知令牌、可信规则判定的敏感数据 | `deny`。目的地绑定的协议认证由受控客户端注入。 |
| PRIV-03 | 不透明密文、无法解包、截断、超过扫描预算、未支持的载荷格式 | `deny`。明确返回无法完整检查。 |
| PRIV-04 | 项目源码、会话、客户数据、个人信息、附件或日志 | 按数据类别和目的地策略判定。文件名、扩展名及来源标签用于分类，实际正文仍接受扫描。 |
| PRIV-05 | 规则需要语义分类 | 仅当分类请求本身通过 §4 的外发约束时调用 Jev。置信度不足、错误或未配置导致该检查无法完成时 `deny`。 |
| PRIV-06 | 完整检查通过，且目的地授权覆盖该数据类别 | `allow` |

本地检测覆盖已知凭据值、敏感文件及路径、私钥和令牌格式、受支持文本与容器格式。缓存键包含内容摘要、扫描器和规则版本；复用扫描结果后仍重新判定目的地。密钥曾被删除的 Git 历史仍须按实际发送对象集扫描。审计记录规则、目的地摘要、数量和脱敏位置。

扫描预算按出口区分：完整模型请求最多 16 MiB，其他出口仍为 1 MiB。准备阶段与 SDK 实际发送阶段使用同一个 `payload_limit()`；完整扫描全部字节，超过上限明确拒绝，不截断历史或跳过尾部。

### 数据类别表

[`network_data.py`](../agent_core/integrations/network_data.py) 的 `ALLOWED_DATA` 是唯一允许表；`FORBIDDEN_DATA` 优先拒绝。来源类别由受信任代码提供，不接受模型参数自报。实际 origin 由各传输客户端单独绑定和复核。

| 出口 | 允许的来源类别 |
|---|---|
| HTTP | public、project、attachment |
| 搜索 | public |
| MCP | public、project |
| 配置的模型服务 | public、project、conversation、attachment |
| Jev | public |
| Git | public、project |
| 遥测 | metrics |

`credentials / internal / personal / customer / unknown` 和未登记出口默认拒绝。受保护文件由现有 `protected_paths` 标记；读取结果仅在模型请求副本中剔除，本地历史不修改。普通正文继续做格式、凭据、编码和预算检查；这不等同于自动识别所有个人或客户隐私。需要语义判断时遵守 §4，不能把未知敏感原文送去分类。

### Git 推送执行边界

1. 读取可信远端映射及准确 refspec，固定本地提交。连接前检查远端 URL、协议与认证。拒绝未支持 helper、重写及重定向。
2. 根据远端协商确定对象集。至少覆盖将新增的提交、树、blob、注解标签、提交与引用元数据。远端已有对象集合无法证明时，扫描全部相关可达对象。
3. 检查对象后再上传，固定源对象与目标引用。任何变化重新检查。受控的 Git `pre-push` 可用于对象上传前检查；远端连接校验必须先完成。
4. 拒绝时验证没有对象外发、没有远端引用变化。首次推送、普通增量、已从工作树删除但仍在历史的敏感内容都应有测试。

[Git pre-push 文档](https://git-scm.com/docs/githooks#_pre_push)

## 4. Jev · OpenRouter Decisions API

在现有 [provider.jsonc](../agent_core/provider.jsonc) 的 `models` 中登记 Jev。沿用 `load_provider_config()` 和现有的 `OPENROUTER_API_KEY`。模型 ID 和超时分别读取该条目的 `model_name` 与 `timeout`。

```jsonc
// 合并到 provider.jsonc 的 models
"jev": {
  "provider": "openrouter",
  "protocol": "decisions",
  "model_name": "<OpenRouter Decisions 模型 ID>",
  "timeout": 10
}
```

开发时扩展 `ModelConfig`，将 `decisions` 限定为 OpenRouter 协议。隐私检查读取 `models["jev"]`，聊天默认模型与档位禁止引用 Decisions 条目。置信度阈值归共享隐私规则管理，经本地验证集校准。模型或规则版本变化使分类缓存失效。

```python
POST https://openrouter.ai/api/alpha/decisions
Authorization: Bearer <OPENROUTER_API_KEY>

{
    "model": config.models["jev"].model_name,
    "state": sanitized_state,
    "questions": configured_classification_questions,
}
```

- 使用 Decisions 的结构化 `choice` 或 `score` 结果。请求与聊天 LLM、`/chat/completions`、Pydantic AI 聊天会话分离。按预期标签、分值区间和置信度校验响应。
- Jev 请求本身也是外发。先用本地确定性规则判断，原始候选载荷只有已获准发往该分类服务时才能发送。其余场景仅传可外发的脱敏特征；特征不足以判定时阻止原请求。
- 分类请求使用专用目的地规则和本地前置检查，避免再次调用 Jev。Jev 输出是判定证据，最终结果由隐私规则合成。超时、异常或低置信度按 PRIV-05 处理。

[OpenRouter Jev 与 Decisions API](https://openrouter.ai/blog/tutorials/how-to-use-jev/)

## 5. 开发与验收

- [ ] 接入共享 `before_tool_execute`、网络工具登记和调用授权。验证本地工具跳过网络检查，联网工具在拒绝时不执行。用本地接收端证明敏感请求零发送，扫描与接收字节一致，重定向和重试重新检查。
- [ ] 实现 NET-01/02 和 NET-04。测试 URL 与头部泄漏、文件/表单/stdin、加密和截断载荷、Git 首推与增量、提交后删除的密钥、引用竞态及远端保持原状。
- [ ] 接入 NET-05/09/10/12。HTTP MCP、模型回退、搜索查询和遥测均能观察实际受控请求。用测试代理验证脚本与 stdio MCP 不能绕过出口。
- [ ] 用模拟服务验证 Jev 读取 `provider.jsonc` 的模型与超时，复用 `OPENROUTER_API_KEY`，调用 Decisions 路径。验证聊天档位拒绝引用 Decisions 模型，异常或低置信度拒绝，敏感原文未进入分类请求或日志。
- [ ] 为 WebFetch、网页搜索和 MCP 分别测试本地放行、硬拒绝及语义分类。前两种结果的 Jev 调用次数必须为零。
- [ ] 对其余 NET 行逐项登记已接入适配器或明确拒绝。新增出口须附允许、拒绝、变更重验三类用例。

## 6. 当前实现范围

| 入口 | 已实现边界 |
|---|---|
| before_tool_execute | 复用权限 Hook；未登记工具拒绝执行。内置本地工具按 `INTERNAL` / `file_system.*` 登记；新增本地 Python 工具须有可信 `metadata={"ani_network": "local"}`。动态 MCP 绑定服务 ID 与配置指纹。 |
| curl · NET-01/02 | HTTPS GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS；内联、JSON、URL 编码、文本文件、multipart 表单和可检查的自定义头。Hook 固定请求字节；执行不重读文件。上传拒绝跨工作区、符号链接、保护 glob 和特殊文件。DELETE 仍走操作确认。 |
| HTTP 不支持形态 | 不透明二进制、持续 stdin、认证或路由头、压缩请求、代理、Shell 组合、后台任务与输出文件选项明确拒绝。GET/HEAD 重定向逐跳复查；上传不重定向，携带自定义头时不跨 origin 重定向。 |
| Git push · NET-04 | 单个分支或完整标签 refspec、`--force`、`--delete`；强推与删除保留操作确认。固定远端、源对象和引用；独立 bare 仓库扫描可达历史、标签和元数据。支持 GitHub 的 GH_TOKEN/GITHUB_TOKEN 绑定认证，不把凭据放进命令参数。 |
| Git 不支持形态 | SSH、任意 helper、LFS、批量 refspec、mirror、lease 等未实现形态明确拒绝。LFS 指针在连接前拒绝，避免只推引用而漏传对象。 |
| MCP · NET-09 | 参数与 HTTP 正文检查；每次发送复核服务启用状态和配置指纹，DNS 解析后再查一次。HTTP 初始化/发现使用相同传输。stdio 禁止自行联网及委托外部应用联网。 |
| 模型 · NET-10 | 所有 provider 构造的 SDK 客户端统一检查最终请求字节、非认证头、重试与真实 origin，固定 DNS、禁代理和重定向。公网模型域名不允许解析至内网；显式配置的本机/内网 IP 网关单独绑定。暂不支持图像/PDF 等二进制外发扫描。 |
| 遥测 · NET-12 | 投影白名单内的操作类别、内置工具名、数值用量与耗时；剔除其他原始名称、资源属性、事件、异常详情、命令与正文。LOGFIRE_TOKEN 可选，沿用现有 LOGFIRE_BASE_URL 与 SDK 区域解析，使用受控 HTTP 出口。 |
| Jev · NET-11 | 使用现有 provider.jsonc 的 models.jev 与 OPENROUTER_API_KEY。独立 Decisions API；低置信度、异常或配置错误拒绝。本地结果和分类结果仅缓存摘要，规则/模型变化使缓存失效。 |
| NET-03、06–08、13–16 | [`shell_network.py`](../agent_core/integrations/shell_network.py) 按命令族登记明确拒绝；本地子命令继续受限执行。未知脚本、构建及 stdio 子进程无直接网络出口。 |
| NET-17 | 当前无自动更新或后台同步入口；新增时必须注册并接入同一判定。 |

验证代码：`test_network_privacy`、`test_network_requests`、`test_network_git`、`test_network_registry`、`test_network_telemetry`、`test_mcp_network`、`test_permission_sandbox`。真实模型服务、Jev 账单与远端 Git 写入未作为离线测试的一部分调用。
