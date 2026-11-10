# Vespera Desktop

Rust + GPUI Kit 的 macOS 静态桌面界面，按 `vespera.sketch` 实现。
「状态 / 产物 / 记忆」在同一个窗口的第三栏切换。

Vespera 的按钮、图标和链接悬停时使用默认箭头光标，不使用手形指针（`cursor_pointer`）。输入框保留文本光标。

## 运行

需要 macOS 14+、Xcode Command Line Tools 和 Rust 工具链。

```sh
cargo run
```

## 打包 macOS App

```sh
python3 scripts/package_app.py
open dist/Vespera.app
```

生成的 `.app` 包含原生 Rust 可执行文件、白底三叶图标和嵌入资源。
运行时不需要 Python、coder-cli、仓库目录或网络服务。
包使用本地 ad hoc 签名，未做 Developer ID 签名与公证。

## 当前范围

- 三栏布局、分组聊天历史、Markdown 回答、附件缩略图和输入区。
- 状态页的消息时间线、文件树、运行上下文与消耗圆环。
- 产物页的文本增删、图片预览、视频封面、音频波形与元信息。
- 记忆页的用户记忆与项目记忆 Markdown。
- 原生 macOS 窗口控制，第三栏 Tab 切换与内容滚动。
- 启动时渐变在 2 秒内收拢，白色三叶标志在后半段旋转放大。收拢后底板继续流动 1 秒，再进入主界面。期间播放启动音效。
- 主界面使用静态示例数据，不连接 Agent，不发送消息，不录音或播放聊天附件。

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
