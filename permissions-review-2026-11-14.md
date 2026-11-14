# Ani Bot 权限规则审查

审查日期：2026-11-14。范围为 `agent_core/agent/permissions/`、角色与委派、HTTP/MCP/Git 网络出口及对应 Spec。基于当前源码，包含刚完成的三种只读 `git branch` 形式修复。

原始审查阶段未修改权限源码、配置或应用包。验证使用纯分类器、临时目录、确定性文件替换、模拟后端和模拟 HTTP transport；没有真实外发、推送或修改用户 Git 仓库。未运行真实 `sandbox-exec` 验证，因此沙箱相关结论以生成策略与执行调用链为依据。

## 安全边界修复状态（2026-11-14）

用户授权“先修安全边界”后，已完成下列源码修复。后文保留原始发现与复现证据；其中旧行号对应审查时源码。

| 原始项 | 当前修复 |
| --- | --- |
| 1 文件读取竞态 | 新增描述符绑定解析及身份复核；读取通过已打开的普通文件描述符执行，等待锁及读取期间的路径替换会拒绝返回。目录枚举和原子写入也绑定目录描述符，取消时保留锁至后台 I/O 退出。 |
| 2 嵌套 Git 元数据 | 文件权限、模型内容投影、上传文件与破坏性 Shell 目标检查共用保护路径匹配，覆盖嵌套 `.git` 及受保护祖先目录。 |
| 3 Git 历史来源 | 将当前保护规则传入对象准备，遍历全部待发送提交的历史树与标签来源；已删除文件和多个路径共用 blob 均保留路径检查，拒绝发生在 DNS 和推送之前。旧调用签名也读取受信任保护配置。 |
| 4 编码扫描 | 在层数、候选数量及总字节预算内递归检查 JSON、URL 编码和标准/URL-safe Base64；覆盖短已知凭据、省略 padding、标准 Base64 的 `+`，拒绝重复 JSON 字段及超预算输入。 |
| 5 MCP 请求头 | 扫描实际普通请求头及重复字段；认证豁免仅限可信配置提供的指定协议头的精确名称/值，并绑定原服务 origin。 |
| 6 委派分配 | 校验阶段只形成固定目录和提交基线计划；原生工具执行体的 workspace resolver 在权限通过后分配。显式入口走同一原生校验、审批与票据链，拒绝、等待或审批取消均不分配。 |

验证：分批运行受影响增量用例，共 **126 个测试、218 个子测试通过**，包括新增安全回归、已有 Shell/网络/权限兼容用例及四个原生多 Agent 集成用例。已确认导入 Ani 的 `agent_core` 实现；测试仅使用无害标记、模拟网络与临时本地 Git 仓库。委派安全用例模拟分配器，多 Agent 兼容用例使用临时仓库和临时 worktree；没有真实网络外发或推送。

边界：这轮没有重写 `make_dir/remove/stat/exists` 的完整文件系统实现，没有验证真实 `sandbox-exec`，也没有把确定性替换测试表述为覆盖所有操作系统竞态。内容扫描仍是有限格式和预算内的防护，不能识别任意自定义编码。第 7–14 项功能/体验问题保留待后续处理；应用未打包，改动未提交。

## 审批、执行能力与参数分类修复（2026-11-14）

用户授权处理后续两阶段后，第 7–14 项已按下列边界落实。后文继续保留原始审查证据，并不代表修复后的现状。

| 原始项 | 当前处理 |
| --- | --- |
| 7、11 文件搜索与批准消费 | Super 及所有内置角色挂载 filesystem-only 搜索适配器。原生 grep/list_files 使用已检查文件读取，目录遍历过滤未授权保护后代；精确保护文件批准由同一 backend 消费，不放开任意 Shell。grep/search_files 的保护来源结果也从模型外发投影中移除。 |
| 8 不可执行却询问允许 | 不支持的 Git 变更返回 deny/unsupported，复合命令中未知阶段不能掩盖它。隔离关闭或后端不可用时，在申请本地执行批准之前终止；stdio 也在审批前检查隔离能力。受控 Git push 继续走已登记网络出口。 |
| 9 构建/测试写入 | 新增可信配置 shell_scopes，以精确命令、可选工作区及相对输出目录形成实际执行范围，逐次展示并批准。未声明路径只读，保护资源及网络限制继续生效；缺失祖先的实际创建范围明确展示并绑定身份。没有自动给所有构建脚本工作区写权限。 |
| 10 嵌套仓库 | 暂存、提交、初始化携带目标仓库目录；执行前验证 worktree/git-dir/common-dir、反向绑定及文件身份。只对选定仓库授予元数据例外，保留其他子仓库保护，执行前再次复核。 |
| 12 curl 重建 | 重复 --json 无分隔拼接，传统 data 维持 &；两类混用明确拒绝，最终字节再次扫描。 |
| 13 多目标分类 | truncate 按 size/reference 参数语法提取所有目标；mkdir 也展示全部创建范围。缺值、未知参数与动态目标不获得误分类的项目写入能力。 |
| 14 查询与记忆 | 补齐明确的 branch/tag/remote 查询和 find/grep/sed 读取语法；变更、执行、输出及未知参数不继承读取放行。记忆资格按所有匹配规则计算，与 Spec 一致。 |

最终受影响增量验证为 **136 个测试、273 个子测试通过**。其中真实 macOS sandbox-exec 验证了声明输出可写、相邻未声明文件不可写、嵌套仓库和 linked worktree 能暂存提交，以及选定仓库的授权不能改写其他仓库元数据。另覆盖原生多 Agent、网络出口及前轮安全回归；没有真实网络发送或推送，没有运行全量测试。4 条非失败警告来自已导入模块的 pytest 重写与现有 Skill frontmatter。

保留边界：filesystem-only 搜索只支持 context=0、file_type=null，使用 glob 筛选类型，不应用 .gitignore；这些限制写入工具描述/schema 并在审批前校验。Git reset/restore/checkout/rebase 及未支持的 branch/tag/remote 变更仍不执行；支持范围之外的命令参数继续保守分类。用户配置未被修改，应用未打包，改动未提交。

## 结论

主要问题是三类边界没有保持一致：文件工具与 Shell 对保护路径的理解不同；审批决定与执行器可授予的能力不同；网络扫描正文时丢失编码规范化或文件来源信息。继续逐条增加命令白名单只能解决部分误拦。

下列 14 项按修复优先级和关联性整理。P1 表示可突破预期保护边界；P2 表示明确的授权/执行矛盾、功能错误或检查缺口；P3 表示较低优先级的规范及覆盖不足。没有把拒绝未知网络协议、禁止直接关闭沙箱、禁止只读角色执行任意项目代码等既定限制列为缺陷。

## 1. P1：文件读取的检查与实际打开之间存在路径替换窗口

- 位置：[workspace.py:48](agent_core/agent/permissions/workspace.py:48)、[workspace.py:75](agent_core/agent/permissions/workspace.py:75)。
- `check()` 对真实路径做检查后，`read_bytes()` 仍将原始路径交给异步父后端。外部进程可在实际打开前替换文件或祖先目录。
- 验证：只在临时目录中，在父后端文件锁的确定性时机将普通文件替换为指向模拟 forbidden 文件的符号链接，真实读取返回该模拟文件的无害标记；随后重新检查同一路径则明确拒绝。
- 影响：可能越过工作区及硬保护目录。应用自己的执行租约不能锁住外部进程的文件系统变更。
- 修正方向：用绑定目录/文件身份的描述符完成解析和读取，并在实际打开位置落实校验。不能仅在 await 前增加一次 `resolve()`。
- 验证限制：没有启动真实竞争进程；没有扩展验证写入祖先目录替换。

## 2. P1：默认保护漏掉嵌套仓库的 Git 元数据子文件

- 位置：[config.py:32](agent_core/agent/permissions/config.py:32)、[rules.py:108](agent_core/agent/permissions/rules.py:108)、[workspace.py:59](agent_core/agent/permissions/workspace.py:59)。
- 工作区为非 Git 父目录、下面有多个仓库时，`repo/.git/config` 的相对路径不匹配 `.git/**`，basename `config` 也不匹配 `.git`。
- 验证：临时目录中的 `repo/.git/config` 对读写文件工具均为 allow，真实 backend 读取及替换无害标记均成功。没有建立或修改真实仓库。
- 影响：文件工具能直接改嵌套仓库配置、引用、索引或 hook 文件，而 Shell profile 会递归保护这些路径。同一配置在不同工具上含义不同。
- 修正方向：统一路径组件匹配语义，覆盖根仓库、嵌套仓库和 worktree 的 `.git` 文件；不能只依靠最后一个文件名。

## 3. P1：Git 推送检查丢失受保护文件的路径来源

- 位置：[git_push.py:155](agent_core/integrations/git_push.py:155)、[git_push.py:164](agent_core/integrations/git_push.py:164)、[git_push.py:173](agent_core/integrations/git_push.py:173)。
- 对象枚举使用 `--no-object-names`，blob 一律按 `project` 扫描，没有带入当前 `protected_paths`，也没有建立历史路径与 blob 的关联。
- 验证：完全模拟 Git 对象命令，待推送历史中的 `credentials.txt` 含普通无害标记文本时，真实对象准备流程通过；相同文件经 HTTP 上传文件检查会因默认保护规则被拒绝。
- 影响：敏感文件正文即使不包含可识别的令牌格式，也应受文件来源保护；当前可能经 Git 出口通过，包括已从工作树删除但仍在历史中的文件。
- 修正方向：把生效保护配置传入推送准备流程，保留所有待发送历史树的路径与对象关系，再按来源类别及正文检查。
- 验证限制：没有真实 Git 推送或网络连接。

## 4. P1：编码扫描在规范化后没有继续执行完整检查

- 位置：[network_privacy.py:26](agent_core/integrations/network_privacy.py:26)、[network_privacy.py:66](agent_core/integrations/network_privacy.py:66)、[network_privacy.py:79](agent_core/integrations/network_privacy.py:79)。
- JSON 规范化后只重查明文秘密，没有再次进行完整的编码检测；Base64 候选另有至少 24 字符的门槛。
- 验证：使用无害示例凭据，明文和直接编码载荷被拒绝，而某些 JSON 转义与编码组合通过；较短已知环境凭据的编码形式也通过。真实请求准备与模拟 transport 确认这些字节会到达发送层。
- 影响：共享隐私检查对已支持的数据形式存在可复现漏检，不能作为所有凭据外发的完整保证。
- 修正方向：在总字节、候选数量和层数预算内，对每一层规范化结果复用同一套检查；已知凭据的编码形式不能仅因长度较短而跳过。
- 验证限制：未使用真实凭据或真实外发。

## 5. P2：MCP 的实际非认证请求头没有被隐私扫描

- 位置：[network.py:81](agent_core/integrations/network.py:81)、[network.py:94](agent_core/integrations/network.py:94)。
- transport 检查 URL 与正文，但将实际 headers 直接保留并发送。
- 验证：正文合法、普通自定义头含无害敏感示例时，请求到达模拟底层 transport；相同内容放进正文会被拒绝。
- 影响：依赖可信 MCP 配置提供该头，不代表模型可以任意改配置；但误配和环境变量展开后的非认证头缺少最后一道出口检查。
- 修正方向：检查实际非认证头；仅对受控客户端注入、绑定具体服务 origin 的协议认证保留窄豁免。

## 6. P2：委派分配 worktree 发生在权限检查前

- 位置：[delegation.py:191](agent_core/agent/delegation.py:191)、[delegation.py:220](agent_core/agent/delegation.py:220)、[worktrees.py:53](agent_core/storage/worktrees.py:53)。
- `before_tool_validate` 已调用 `role_workspace()`，后者可执行 worktree 分配；权限拒绝在后续 `before_tool_execute` 才发生。
- 验证：替换实际分配器为记录调用的无副作用 mock，明确 deny `delegate_task` 时仍先发生一次分配调用。真实分配器静态确认会创建目录、调用 `git worktree add` 并写 manifest。
- 影响：用户拒绝或规则禁止委派，也可能留下目录和 Git 管理信息变更。
- 修正方向：校验阶段只形成无副作用计划；权限通过后再分配资源，并验证执行目录身份。
- 验证限制：没有实际创建 Git worktree。

## 7. P2：只读角色被允许使用 grep，但底层实现不能运行

- 位置：[roles.py:15](agent_core/agent/roles.py:15)、[policy.py:88](agent_core/agent/permissions/policy.py:88)、[workspace.py:168](agent_core/agent/permissions/workspace.py:168)。
- Reviewer/Architect 的工具与权限允许文件搜索。已安装 Harness 的 `run_ripgrep()` 实际通过 `workspace.run()` 启动受控搜索；backend 对只读角色在入口就拒绝所有进程。
- 验证：核对依赖调用链，模拟只读角色 backend 执行搜索入口立即得到“当前角色不能执行命令”，未启动子进程。
- 影响：正常只读代码审查的搜索能力失效，不能靠批准工具调用解决。
- 修正方向：区分框架内部受控读取进程与模型任意 Shell，或使用不依赖通用命令执行入口的搜索后端。不要直接向只读角色开放任意 Shell。

## 8. P2：无法执行的操作仍展示“本次允许”

- 位置：[shell_rules.py:256](agent_core/agent/permissions/shell_rules.py:256)、[policy.py:277](agent_core/agent/permissions/policy.py:277)、[workspace.py:179](agent_core/agent/permissions/workspace.py:179)。
- `git reset`、`git restore`、`git checkout` 等被分类为 `ask, supported=False`，审批后 backend 仍立即抛出 PermissionError。
- 验证：真实分类器加模拟执行后端，放入代表已获批调用的 grant，仍在启动进程前拒绝。
- 影响：审批界面承诺了实际无法授予的操作，造成重复授权和重试。
- 修正方向：不可落实约束时明确返回 deny/unsupported；只有能够消费批准并执行的操作才返回 ask。

## 9. P2：获批的构建/测试命令拿不到所需写入范围

- 位置：[shell_rules.py:23](agent_core/agent/permissions/shell_rules.py:23)、[rules.py:106](agent_core/agent/permissions/rules.py:106)、[workspace.py:189](agent_core/agent/permissions/workspace.py:189)。
- `npm run build`、`python -m pytest`、`mkdir build` 被归为无目标 UNKNOWN。批准后写入仅限任务目录和 scratch，项目构建目录、缓存或测试产物不在授权中。
- 验证：纯分类和模拟 backend 的真实 profile 生成均确认没有项目写入目标；full 加显式 allow 仍先命中强制 ask。
- 影响：需要项目写入的任务无法因用户批准而完成。纯读取或只用 scratch 的测试不必然失败，不能声称所有测试都会失败。
- 修正方向：提供可展示并绑定的声明式执行范围或受控构建配置，明确批准哪些输出目录；不要笼统自动放行任意脚本。
- 验证限制：没有运行真实构建或真实 sandbox-exec。

## 10. P2：工作区内嵌套仓库无法正常获得 Git 写授权

- 位置：[shell_rules.py:258](agent_core/agent/permissions/shell_rules.py:258)、[workspace.py:132](agent_core/agent/permissions/workspace.py:132)。
- Git add/commit 要求 `cwd == root`，而 profile 的元数据授权也只围绕根目录仓库构造。
- 验证：`git -C repo add .`、`cd repo && git commit -m fix` 都变成无目标 UNKNOWN，批准后的 profile 无 Git 元数据写权限；根目录 `git add .` 则正确识别为 GIT_WRITE。
- 影响：当前 learn-agent 这种非 Git 父工作区含多个子仓库的结构尤其容易遇到；正常暂存/提交会陷入询问后仍无能力执行。
- 修正方向：以已验证的目标仓库为授权对象，分别绑定工作树、git-dir/common-dir，继续禁止工作区外目标。

## 11. P2：受保护文件 grep 的批准没有被 Shell profile 消费

- 位置：[workspace.py:54](agent_core/agent/permissions/workspace.py:54)、[workspace.py:153](agent_core/agent/permissions/workspace.py:153)、[workspace.py:208](agent_core/agent/permissions/workspace.py:208)。
- coding 角色批准 `grep(path='.env', ...)` 后，文件 backend 接受精确 grant，但搜索子进程的 profile 仍无条件禁止读取匹配保护 glob 的文件。
- 验证：相同精确目标通过 `backend.check()`，生成 profile 却保留覆盖它的 deny；依赖确认 grep 会进入 workspace.run。
- 影响：read_file 可审批读取，grep 却在批准后仍失败。同一资源的授权语义因工具实现不同而变化。
- 修正方向：让搜索消费仅覆盖批准目标的读取能力，或通过已授权文件读取实现精确文件搜索，继续保留其余保护。
- 验证限制：未运行真实 sandbox-exec。

## 12. P2：受控 curl 的重复 --json 参数被改写为错误请求体

- 位置：[shell_http.py:115](agent_core/integrations/shell_http.py:115)、[shell_http.py:158](agent_core/integrations/shell_http.py:158)。
- 所有 data 参数统一以 `&` 拼接，但 curl 的重复 `--json` 是无分隔追加。
- 验证：无网络调用的 `prepare_curl` 将两段合法 JSON 片段重建为带额外 `&` 的无效 JSON；本机 curl 手册确认重复参数的拼接语义。
- 影响：扫描、审批绑定的字节与用户命令表达的请求不同，正常 API 请求会失败或产生不同业务行为。
- 修正方向：保留各参数的拼接语义并检查最终字节；暂不支持的混用组合应明确拒绝，不能静默改写。

## 13. P3：多目标 truncate 只确认最后一个文件

- 位置：[shell_rules.py:210](agent_core/agent/permissions/shell_rules.py:210)。
- `truncate -s 0 first.txt second.txt` 的 targets 仅包含 second.txt；对照 rm 多文件可正确产生两个目标。
- 影响：确认清单不完整，沙箱会阻止未授权目标，而受批准的后续目标仍可能被修改，形成部分执行。
- 修正方向：按命令参数语法提取全部受影响文件；暂不支持多目标时应在执行前明确拒绝。
- 验证限制：仅对真实分类器做无副作用验证，未运行 truncate。

## 14. P3：普通只读查询及记忆资格仍有覆盖/规范不一致

这是两个较低优先级的独立点：

1. 只读查询覆盖：当前修复只精确支持三种 branch 形式。`git branch -a`、`git branch --list 'feature/*'` 仍为 unsupported；`git tag --list`、`git remote -v`、纯查询 find/grep/sed 示例也继续 ask。位置：[shell_rules.py:188](agent_core/agent/permissions/shell_rules.py:188)、[shell_rules.py:254](agent_core/agent/permissions/shell_rules.py:254)。已用纯分类器验证。应按命令参数和副作用识别读取形式，不能将整个命令族一律放行。
2. 记忆资格：Spec 要求“所有命中项均允许记忆”，[rules.py:116](agent_core/agent/permissions/rules.py:116) 实际只检查 ask 命中项。普通读取同时命中 ask/remember=true 和 allow/remember=false 时，仍可获得记忆资格。应明确产品含义并统一 Spec/实现；这不是显式 deny 被绕过。未驱动真实多轮界面。

## 验证与未发现的问题

- 对 Ani 当前实际导入的分类器运行 CLI 源项目的 Shell 分类增量测试，以及 Ani 的分支权限测试：13 项测试、62 个子用例通过；有一条与 logfire 模块已导入有关的 pytest 重写提示，不影响测试通过。
- 现有测试通过不覆盖上述新增场景，尤其是角色文件搜索、嵌套 .git 文件保护、拒绝委派前的副作用，以及网络来源/编码组合。
- 显式 deny 优先、一次性票据消费、配置变更后指纹失效、HTTP 批准绑定最终请求体、MCP origin/配置复核等机制本身有实际落实，不应因本报告而整体移除。
- 未发现所有只读角色或一次审批均可被普遍绕过；问题按各项具体触发条件成立。

## 修复顺序

先修文件硬保护与打开时校验、Git 外发的来源判定和编码扫描，再补齐 MCP 非认证头检查及委派执行顺序。之后统一“可执行、需审批、不支持”的状态，解决文件搜索、声明式构建范围和嵌套仓库授权。最后补参数覆盖、请求重建及 Spec 一致性用例。
