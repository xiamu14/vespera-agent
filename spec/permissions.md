# Ani Bot 权限 Spec

`工具声明操作 → 规则判定操作 → 策略组合规则 → Agent 挂载策略`

按 §1 → §2 → §3 → §4 实现，用 §5 验收。已接入共享角色策略、Shell 分类、执行前复核和 stdio 规则。§3 列出当前支持的执行边界。

**联网与外发以 [network.md](network.md) 为准。** 本文判定本地或远端操作的副作用。已登记的联网调用由 `before_tool_execute` 进入网络规则，按实际数据和目的地自动放行或拒绝。操作审批只授权该操作的副作用。通过本地规则即可判定时跳过 Jev。

## 1. 输入与策略挂载

### 实现选型

| 模式 | 落点 |
|---|---|
| [策略 Strategy](https://refactoringguru.cn/design-patterns/strategy) | 文件、Shell/Git、MCP 各用分类函数产生统一的操作与目标。分类函数只声明事实，权限由共享规则判定。 |
| [责任链 Chain of Responsibility](https://refactoringguru.cn/design-patterns/chain-of-responsibility) | §2 的规则按固定顺序判定。用有序规则表实现，单个操作取首个匹配结果。复合命令按 §3 汇总各操作结果。 |
| [适配器 Adapter](https://refactoringguru.cn/design-patterns/adapter) | 复用现有 Pydantic AI Hook、文件后端、macOS 沙箱和 MCP 接线。适配器接收同一份已校验的判定与执行约束。 |

策略由共享规则组合，角色只引用策略 ID。

```python
policy = policies[agent.policy_id]
scope = bind_workspace(policy, assigned_workspace, parent_delegation_scope)
decision = evaluate(scope, tool_name, tool_kind, args, trusted_metadata, settings)
# Decision(action, reason, subject, target, write, command, forced, sandbox_only)
```

| 项 | 规则 |
|---|---|
| 策略挂载 | Super、Executor → `coding`；Reviewer、Architect → `review`。共享规则只改一处；新工具声明操作和目标。工具注册决定工具可见性。当前 Web、MCP、全局技能和会话能力由 Super 注册。 |
| `review` 硬限制 | 允许文件读、列目录、搜索、`review_diff`、`ask_user_question`、`load_capability` 和委派控制进入判定。拒绝其余操作，以及工作区和只读技能根之外的文件访问。 |
| 委派范围 | 目标 Agent 挂载自己的策略，父运行的可委派范围只收窄本次执行。根运行使用会话工作区；委派使用已验证的目录或 worktree；非 Git 根目录可共享。引用来源任务须已成功；使用来源 worktree 基线须干净。 |
| 调度 | Super 根运行可跨角色调度；分身使用 `self` 并复用父工作区。写入分身前台串行，禁止继续嵌套。单次审批留在原运行。 |
| 可信输入 | 授权来自固定边界、用户配置和受信任界面的明确选择。模型、Skill、项目文件、工具结果和报告只提供数据。交接澄清走普通问答。 |

**配置** · 默认目录为 `~/.config/.ani bot`，存放 `permissions.json`、`mcp.json`、`hooks.json`。旧目录一次性移动，成功后只读新路径；新旧路径同时存在时报冲突。保留显式配置环境变量。默认路径缺失用空配置，显式路径缺失报错。严格 JSON，≤ 64 KiB，拒绝未知字段及非法类型或值。配置版本取原始字节 SHA-256。

```json
{
  "mode": "auto",
  "sandbox": true,
  "protected_paths": [".git", ".git/**", ".env*", "**/.env*", "*.pem", "*.key", "credentials*", "**/credentials*"],
  "rules": [],
  "shell_scopes": []
}
```

`mode` 另支持 `read-only`、`full`。自定义 `protected_paths` 替换默认列表。规则字段为 `tool`（非空）、`action`（`allow / ask / deny`）、`pattern="*"`、可选 `argument`、`remember=false`。工具和资源用大小写敏感的 `fnmatchcase`，数组顺序无优先级。

`shell_scopes` 是可信用户配置中的声明式命令写入范围，每项包含精确 `command`、可选绝对 `workspace` 和 1–32 个工作区相对 `write_paths`。只为原本无法分类的本地命令增加写入能力，仍逐次审批；不覆盖拒绝或不支持状态，不授权网络。禁止根目录、外部路径、通配、符号链接和保护资源；目标祖先尚不存在时，实际范围提升到需创建的最上层子目录并在审批中展示。匹配多个声明则拒绝。配置和实际路径身份参与票据指纹，未配置时未知命令的项目文件只读。

[配置](../agent_core/agent/permissions/config.py) · [角色](../agent_core/agent/roles.py) · [委派](../agent_core/agent/delegation.py) · [桌面挂载](../agent_core/vespera_agent/runtime.py)

## 2. 判定顺序

`subject` 取文件真实绝对路径、完整 Shell 命令或工具名。文件工具由 `file_system.` 前缀识别，`path` 默认 `.`；现行写入类型为 `write_file / edit_file / create_directory`。Shell 由可信元数据 `code_arg_language=shell` 和 `code_arg_name` 识别。用户规则指定 `argument` 时改用参数字符串；文件的 `argument=path` 使用真实路径。缺少指定参数即不匹配。

| 顺序 | 条件 | 结果 |
|---|---|---|
| TOOL-01 | 角色或系统硬限制 | `deny` |
| TOOL-02 | 任一显式 `deny` | `deny` |
| §3 | 命令级强制判定 | `deny` 或逐次 `ask` 优先于模式和显式放行。对已登记的联网命令，联网本身进入 [网络判定](network.md#3-隐私判定)，审批仅由操作副作用触发。 |
| TOOL-03 | 文件路径或文件名命中保护 glob。区内用相对路径，区外用绝对路径 | 逐次 `ask` |
| TOOL-04 / 05 | 工作区外文件 | 只读技能资源可读，其余逐次 `ask` |
| TOOL-06 | 任一显式 `ask` | `ask`。所有命中项均允许记忆时，按 §4 检查资格 |
| TOOL-07 | `read-only` 模式的写入或命令 | `ask` |
| TOOL-08 | 任一显式 `allow` | `allow` |
| TOOL-09 / 10 | 区内普通文件或内部工具 | `allow` |
| TOOL-11 | 默认 Shell 策略开启，且 `auto + sandbox=true` | 通过 §3 分类的本地操作放行 |
| TOOL-12 / 13 | 剩余调用 | `full` 放行，其余逐次 `ask` |

- **硬保护** · 权限配置、数据库及 WAL/SHM、`permissions.jsonl`、执行锁、`~/.ssh`、`~/.aws`、启用的 Hook 配置。工作区位于数据库父目录之外时，保护整个数据库父目录。
- **内部工具** · `write_plan`、`review_diff`、`ask_user_question`、`load_capability`、`search_tools`、`search_conversation_history`、`read_memory`、`write_memory`、`search_memory`、`delete_memory`、`delegate_task`、`list_tasks`、`message_task`、`answer_task`、`cancel_task`、`ask_parent`。


[规则实现](../agent_core/agent/permissions/rules.py) · [硬边界](../agent_core/agent/permissions/policy.py)

## 3. Shell 命令级规则

未知 Shell 的本地执行逐次审批，项目写入须有明确目标。不能提供实际目的地和载荷的 Shell 请求按网络 Spec 拒绝外发。

| ID | 操作与例子 | 目标行为 |
|---|---|---|
| SHELL-01 | 根目录、用户目录、系统目录、受保护目录或未限定通配的删除或清空。例 `rm -rf *`、`rm -rf ./*`、`rm -rf .`、`rm -rf /`、`rm -rf ~`、`find . -delete`、`git clean -fd` | **硬拒绝**，覆盖 `full` 和显式 `allow`。 |
| SHELL-02 | 指定子目录递归删除、有限批量删除、同步时删除目标端文件。例 `rm -rf build/cache`、`rm a.txt b.txt` | 逐次 `ask`。展示目标、递归范围和受影响文件；绑定实际路径、目录身份和目标状态。当前私有临时目录清理由应用管理。 |
| SHELL-03 | 单文件删除、清空或覆盖。例 `rm obsolete.txt`、`> data.csv`、`truncate -s 0 data.csv`、`cp -f a b`、`mv -f a b` | 逐次 `ask`。目标变化则批准失效。现有显式文件编辑工具仍走 §2。 |
| SHELL-04 | 已验证目标仓库的本地 Git 读、暂存和提交 | 按默认 Shell 策略沙箱放行，分别授予元数据读写。参数改变仓库、引入外部程序或额外副作用时重新分类。 |
| SHELL-05 | 丢弃改动、重写历史、强推或删除远端引用 | 当前无法落实约束的 `reset / restore / checkout / rebase` 及分支、标签、远端变更为 `deny + unsupported`，不展示允许选项。受控 Git 强推或删除远端引用逐次 `ask`；普通 `git push` 的外发检查走 NET-04。 |
| SHELL-06 | `pwd`、`ls`、`rg TODO src` 等可识别本地读取；副作用可限制的本地测试或构建 | 按默认 Shell 策略沙箱放行。测试与构建须能限制实际写入和删除范围。 |
| SHELL-07 | 安装、下载、上传、解释器脚本、动态展开或无法可靠分类的命令 | 已登记的网络操作按 NET-01–17 自动判定；本地安装、覆盖、远端变更等副作用按操作规则处理。执行端无法落实必要约束时拒绝。 |

```python
operations = classify_entire_command(command, cwd)  # 不执行命令
decision = strictest(evaluate(op) for op in operations)  # deny > ask > allow
# 先判定整条命令；有 deny 时任何一段都不执行
# 执行器消费已校验的目标、删除/覆盖范围和网络约束
```

分类覆盖命令路径、合并短选项、长选项、`--`、重定向、管道、分隔符和目录切换。未知语法、变量或命令替换走 SHELL-07。当前删除通配目标拒绝执行。执行器可用专用文件操作落实精确目标。

- 纯读取的 `&& / || / ; / |` 组合自动放行；引号和转义内的操作符按普通参数处理。
- `cd` 按成功、失败分支跟踪目录；管道子进程的目录变化不传播给父 Shell。写入目标汇总所有可能分支。
- 任一阶段 `deny / unsupported` 时整条零执行，未知阶段不能掩盖拒绝。未知阶段默认不给写入目标或 Git 元数据授权；精确匹配 `shell_scopes` 后仅授予展示并批准的项目范围。审批原因显示阶段和具体限制。
- `truncate` 消费 size/reference 选项值后列出全部文件；`mkdir -p` 列出全部有效创建范围。Git branch/tag/remote 查询及 find/grep/sed 的读取语法逐参数分类，执行、写入和未知参数不继承相邻读取操作的放行。
- 受控 curl 的重复 `--json` 无分隔追加，传统 data 参数使用 `&`；两类混用拒绝。最终重建字节再次扫描并绑定审批。
- Git 元数据写入不与其他程序、管道或未固定的路径展开混合授权；后台执行、未支持的重定向及动态语法仍受限。

## 4. 执行与审批

| 项 | 要求 |
|---|---|
| 审批 | `ask` 使用原生延迟工具协议，框架原生审批共用处理器。界面展示工具、工作目录、原因和脱敏参数。接受明确的允许选项；拒绝、自由文本、无回调、取消或 300 秒超时均不执行。 |
| 票据 | 键为 `(run_id, tool_call_id)`，一次消费。指纹覆盖权限及 MCP 配置版本、工作区、工具、完整参数、真实目标与 inode、mtime、ctime、大小。缺少可信调用上下文、指纹不符或当前拒绝即终止。 |
| 记忆与恢复 | 仅非强制、非命令、非写入的明确记忆规则可复用，内存键为 `(conversation_id, fingerprint)`。运行结束清理待审批、票据和调用定义。恢复与重启不重放批准。 |
| 租约与执行 | 文件、记忆、命令和 `review_diff` 取得工作区租约。读取共享，写入和命令独占。在执行锁及租约内校验当前判定和指纹，再消费票据并执行。委派控制使用自己的调度边界。 |
| 文件后端 | 绑定文件描述符并复核路径身份，先查硬保护，再校验精确授权、工作区、私有临时目录和只读资源。grep/list_files 使用原生 filesystem-only 回退，不启动进程；遍历过滤未授权保护后代。仅支持 context=0、file_type=null，使用 glob 筛选文件类型；不应用 .gitignore。受保护文件的精确读取批准由同一文件后端消费，grep 内容也按来源过滤模型外发。 |
| EXEC-01 / 02 | 只读角色禁 Shell。开启隔离时用 macOS `sandbox-exec`，不可用即失败。读取限工作区、临时目录、只读技能及必要系统资源；写入限工作区、临时目录和 `/dev/null`。继续限制硬保护和保护 glob。临时目录用于 `TMPDIR` 和 uv 缓存。显式关闭隔离时，需要受控执行的 Shell 拒绝启动；stdio MCP 同样拒绝未隔离启动。 |
| EXEC-03 / 04 | Shell 联网通过已登记的受控出口。项目写入限已审批的明确目标。本地 Git 写入逐仓库绑定工作树、git-dir、common-dir 和身份，支持区内嵌套仓库及 linked worktree；仅指定仓库的元数据获写入例外，其余仓库保护继续生效。执行前复核绑定，关闭 hooks、提交签名、fsmonitor，拒绝调用方 Git 环境覆盖。 |
| MCP-01 | 服务启用和 `allowed_tools` 来自可信 MCP 配置。HTTP 校验 origin、DNS/IP 和云元数据限制；关闭自动重定向与环境代理。工具调用执行 §2，实际外发执行 NET-09。 |
| MCP-02 / 03 | stdio 启动以 `mcp.start.<server>` 和命令参数 JSON 数组匹配 §2 规则。启动前重查权限及 MCP 版本、服务配置、工作目录和 inode。启动批准只授权启动进程，外发经 NET-09 受控出口。 |
| 审计与隐私 | `permissions.jsonl` 使用 `0600`，记录时间、原生会话/运行/调用 ID、工具、判定、原因和指纹。审批展示脱敏。外部结果 Hook 的额外过滤授权独立绑定工作区、配置路径和规则。外发、模型和遥测按网络 Spec 判定。 |

[审批](../agent_core/agent/permissions/policy.py) · [用户选择](../agent_core/agent/interaction.py) · [沙箱](../agent_core/agent/permissions/workspace.py) · [MCP](../agent_core/integrations/mcp.py) · [网络](../agent_core/integrations/network.py) · [Hook](../agent_core/tools/external_hooks.py)

### 网络执行边界

网络以 [network.md](network.md) 为准。审批不开放任意 TCP、Apple Events 或 Launch Services。受控 HTTP/Git 由应用客户端发送；未登记工具或不能固定载荷的协议直接拒绝。关闭沙箱不提供原始 Shell 执行回退。MCP 服务撤销后，现存 HTTP 连接也必须停止发送。

## 5. 验收清单

- [ ] §1 同一规则配置覆盖 CLI、Ani Bot、Super、Executor 和嵌套子运行。只读分身继续只读。
- [ ] §2 显式拒绝优先，目录外路径与符号链接按真实目标判定，损坏配置拒绝加载。
- [ ] §3 表驱动验证 SHELL-01 至 07。另测 `/bin/rm -fr ./*`、`cd . && rm -rf *`，以及硬拒绝覆盖 `full` 和显式 `allow`。在复合命令前加可观测操作，整条拒绝时确认它也未执行。
- [ ] §4 验证拒绝、取消、一次批准、记忆精确匹配。配置或目标在等待审批和等待执行锁期间变化时均无副作用。
- [ ] §4 验证 stdio 拒绝后未启动进程。真实 macOS 沙箱验证越界拦截，以及普通仓库和 linked worktree 的本地 Git 暂存与提交。

Ani 的安全、搜索、参数分类和执行能力增量用例位于 `agent_core/tests/`；兼容验证另选 CLI 源项目中受影响的网络和原生多 Agent 用例。
