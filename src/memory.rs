use crate::{
    fixtures,
    markdown::{markdown, markdown_surface},
    theme::*,
};
use gpui_kit::*;

fn section(title: &'static str, id: &'static str, content: impl Into<SharedString>) -> Div {
    inspector_section(label(title, 16.)).min_h(px(300.)).child(
        div()
            .px(px(12.))
            .text_size(px(14.))
            .line_height(relative(1.3))
            .child(markdown_surface(markdown(id, content))),
    )
}

pub fn memory() -> Div {
    column()
        .w_full()
        .gap(px(20.))
        .child(section("用户记忆", "user-memory", fixtures::USER_MEMORY))
        .child(section(
            "项目记忆",
            "project-memory",
            fixtures::PROJECT_MEMORY,
        ))
}

pub fn live(data: Option<&crate::inspector::Inspection>) -> Div {
    let mut project = section(
        "项目记忆",
        "project-memory",
        data.map(|data| data.project_memory.clone())
            .unwrap_or_default(),
    );
    if let Some(data) = data {
        project = project.children(data.binding_history.iter().map(|change| {
            let old = change.old_path.as_deref().unwrap_or("—");
            let new = change.new_path.as_deref().unwrap_or("—");
            let reason = match change.reason.as_str() {
                "bound" => "绑定",
                "unbound" => "解绑",
                "deleted" => "已删除",
                "unavailable" => "路径不可用",
                "replaced" => "目录已被替换",
                "renamed" => "改名",
                _ => "变更",
            };
            muted(format!("{reason}  {old} → {new}"), 14.).px(px(12.))
        }));
    }
    column()
        .w_full()
        .gap(px(20.))
        .child(section(
            "用户记忆",
            "user-memory",
            data.map(|data| data.user_memory.clone())
                .unwrap_or_default(),
        ))
        .child(project)
}
