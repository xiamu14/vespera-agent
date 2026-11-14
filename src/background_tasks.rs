use crate::{inspector_collapse::CollapsibleSection, theme::*};
use gpui_kit::*;
use serde::Deserialize;

#[derive(Deserialize)]
pub struct TaskUpdate {
    id: String,
    role: String,
    prompt: String,
    status: String,
    outcome: Option<String>,
    tool: Option<ToolUpdate>,
}

#[derive(Deserialize)]
struct ToolUpdate {
    id: String,
    name: Option<String>,
    status: String,
}

#[derive(Default)]
pub struct BackgroundTasks(Vec<(TaskUpdate, Vec<ToolUpdate>)>);

impl BackgroundTasks {
    pub fn update(&mut self, mut task: TaskUpdate) {
        let tool = task.tool.take();
        let index = self.0.iter().position(|(item, _)| item.id == task.id);
        let tools = if let Some(index) = index {
            self.0[index].0 = task;
            &mut self.0[index].1
        } else {
            self.0.push((task, Vec::new()));
            &mut self.0.last_mut().unwrap().1
        };
        if let Some(mut tool) = tool {
            if let Some(item) = tools.iter_mut().find(|item| item.id == tool.id) {
                tool.name = tool.name.or_else(|| item.name.take());
                *item = tool;
            } else {
                tools.push(tool);
                if tools.len() > 5 {
                    tools.remove(0);
                }
            }
        }
    }

    pub fn section(&self) -> Option<CollapsibleSection> {
        if self.0.is_empty() {
            return None;
        }
        let mut content = column().px(px(8.)).gap(px(12.));
        for (role, title, ink, fill) in [
            ("architect", "Architect", 0x7856ba, 0xf4effa),
            ("executor", "Executor", 0x20845f, 0xd2f8e3),
            ("reviewer", "Reviewer", 0xa95887, 0xfcf1f8),
            ("self", "Super", MUTED, FIELD),
        ] {
            let mut tasks = self
                .0
                .iter()
                .rev()
                .filter(|(task, _)| task.role == role)
                .peekable();
            if tasks.peek().is_none() {
                continue;
            }
            content = content.child(
                column()
                    .gap(px(8.))
                    .child(
                        row().child(
                            label(title, 12.)
                                .text_color(rgb(ink))
                                .bg(rgb(fill))
                                .px(px(6.))
                                .py(px(2.))
                                .rounded(px(DETAIL_RADIUS)),
                        ),
                    )
                    .children(tasks.map(|(task, tools)| {
                        let state = match (task.status.as_str(), task.outcome.as_deref()) {
                            ("waiting", _) => "待确认",
                            ("finished", Some("ok")) => "已完成",
                            ("finished", Some("cancelled")) => "已停止",
                            ("finished", _) => "未完成",
                            _ => "进行中",
                        };
                        column()
                            .gap(px(4.))
                            .child(
                                row()
                                    .gap(px(6.))
                                    .child(
                                        label(task.prompt.clone(), 14.)
                                            .flex_1()
                                            .min_w_0()
                                            .truncate(),
                                    )
                                    .child(muted(state, 12.).flex_shrink_0()),
                            )
                            .children(tools.iter().map(|tool| {
                                let name = match tool.name.as_deref() {
                                    Some("read_file") => "读取文件",
                                    Some("list_files" | "list_directory") => "查看目录",
                                    Some("grep") => "搜索内容",
                                    Some("write_file" | "edit_file") => "修改文件",
                                    Some("shell") => "运行命令",
                                    Some("review_diff") => "检查变更",
                                    Some(name) => name,
                                    None => "工具",
                                };
                                let state = match tool.status.as_str() {
                                    "done" => "完成",
                                    "failed" => "未完成",
                                    _ => "执行中",
                                };
                                muted(format!("{name} · {state}"), 12.).min_w_0().truncate()
                            }))
                    })),
            );
        }
        Some(CollapsibleSection::new(
            "background-tasks-toggle",
            "后台任务",
            content,
        ))
    }
}
