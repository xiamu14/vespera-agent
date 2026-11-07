use crate::{
    fixtures,
    markdown::{markdown, markdown_surface},
    theme::*,
};
use gpui_kit::*;

fn section(title: &'static str, id: &'static str, content: &'static str) -> Div {
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
