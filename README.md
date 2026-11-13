# Ani Bot

Rust + GPUI Kit 的 macOS 静态桌面界面，按 `vespera.sketch` 实现。
「状态 / 产物 / 记忆」在同一个窗口的第三栏切换。

Ani Bot 的按钮、图标和链接悬停时使用默认箭头光标，不使用手形指针（`cursor_pointer`）。输入框保留文本光标。

## 运行

需要 macOS 14+、Xcode Command Line Tools 和 Rust 工具链。

```sh
cargo run
```

## 打包 macOS App

```sh
python3 scripts/package_app.py
open "./dist/Ani Bot.app"
```

打包需要同架构的 Rust 工具链、Xcode Command Line Tools、`uv`、uv 管理的 CPython 3.13，以及同级 `coder-cli/` 的源码和锁文件。
生成的 `.app` 自带 CPython、锁定的 Python 依赖和 Agent Core，可移出仓库运行。
默认使用 ad hoc 签名。提供 `VESPERA_SIGNING_IDENTITY` 可选择 Developer ID 签名，公证在打包后执行。

## Agent Core

`agent_core/vespera_agent` 提供独立 Python API。macOS 应用启动时在后台通过
`agent_bridge::AgentBridge` 加载同进程 Core。打包版读取真实会话，支持文本和图片／PDF 附件、流式回复、停止、工具审批及单选／多选回答。Enter 发送，Shift+Enter 换行。开发预览继续使用设计稿数据。

Bridge 接收 JSON 命令并返回 `{"ok": ...}` 或 `{"error": ...}`。

- `list` 返回会话摘要，`create` 创建空会话，可传入 `work_path` 绑定目录，`open` 按 `conversation_id` 恢复历史和项目记忆。
- `send` 接收 `conversation_id`、`prompt` 和可选的附件路径数组 `paths`，用 `poll` 读取流式事件与最终结果。附件最多 4 个，总计 10 MiB，支持 PNG、JPEG、GIF、WebP、PDF。
- `answer` 接收 `conversation_id`、`question_id`，以及 `selected` 选项数组或 `text`。也可使用 `cancel` 或 `close`。
- `inspect` 返回右栏的只读会话数据。
- `set_work_path` 接收 `conversation_id` 和字符串或 `null` 的 `work_path`。确认原路径删除后，可传 `deleted: true` 并清空绑定。

未绑定时使用会话私有目录。在打开或发送时检查绑定，可自动跟随原父目录内唯一匹配的改名。
外部路径消失且无法匹配时记录为不可用。绑定记录和聊天历史保存在应用数据目录。
从 Finder 启动时，可在 `~/Library/Application Support/Vespera/.env` 配置
`provider.jsonc` 所选服务需要的 `*_BASE_URL` 和 `*_API_KEY`。

每日预算由同一 `.env` 中的 `VESPERA_DAILY_BUDGET_USD` 配置，默认 `1` 美元，
必须为正有限数值。重启应用后生效。侧栏圆环按所有会话当天的真实费用占预算比例显示，
达到或超过预算时显示满环，仍可继续对话；费用不可用时仅显示圆环底色。

## 当前范围

- 三栏布局、分组聊天历史、Markdown 回答、附件缩略图和输入区。
- 状态页的消息时间线、文件树、运行上下文与消耗圆环。
- 产物页的文本增删、图片预览、视频封面、音频波形与元信息。
- 记忆页的用户记忆与项目记忆 Markdown。
- 原生 macOS 窗口控制，第三栏 Tab 切换与内容滚动。
- 启动时渐变在 2 秒内收拢，白色三叶标志在后半段旋转放大。收拢后底板继续流动 1 秒，再进入主界面。期间播放启动音效。
- 打包版支持按项目查看、搜索、新建和打开会话，并显示保存的历史。新聊天可在弹窗中选择工作目录，也可直接确定。按 ⌘R 可重新读取列表和当前历史。右栏显示当前会话的时间线、有限目录概览、模型上下文、今日已知费用、工具写入产物和记忆。缺少计量时显示「—」。

底部遮挡当前为透明渐隐，尚未实现对背后实时内容采样的渐进模糊。

## 界面核对

```sh
cargo run --features visual-check --example visual_check
```

使用 GPUI 的原生 Metal 离屏渲染生成三个 Tab 的截图，并验证 Tab 点击。
输出在 `verification/`，包含 1440×960 三个 Tab 和 1280×800 窄窗口。
同时检查切换时前两栏像素保持一致。无需 Computer Use 或系统录屏权限。

每个手写代码文件保持在 500 行以内，按窗口、侧栏、聊天、状态、产物、记忆、
Markdown 与共用样式拆分。`sketch-scripts/` 保存设计稿操作脚本。

### 滚动性能采样

滚动渐隐在当前帧布局后读取视口偏移，避免滚到边界时出现一帧的遮挡错位。
渐隐的变化直接进入当前帧，只有底部留白的开关变化才触发额外布局。

```sh
cargo test --features visual-check --lib masked_scroll::tests
cargo run --release --features visual-check --example visual_check
```

组件测试验证连续滚轮事件后，渐隐在到达和离开底部的首帧就更新。
界面核对程序向三栏各发送 180 组滚轮输入，每组 3 个事件，报告 CPU 事件分发及
绘制的 p50、p95、最大耗时和超过 16.67 ms 的样本数。实际呈现帧间隔通过
Instruments 的 Metal System Trace 中的 CAMetalDrawable 呈现回调测量。
