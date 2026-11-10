use crate::masked_scroll::MaskedScroll;
use crate::theme::*;
use gpui_kit::*;

fn dot_alpha(index: usize, count: usize) -> f32 {
    if count <= 1 {
        1.
    } else {
        0.65 + 0.35 * index as f32 / (count - 1) as f32
    }
}

fn timeline(history: &[(&'static str, &'static str)]) -> Div {
    inspector_section(label("时间线", 16.)).child(column().gap(px(14.)).children(
        history.iter().enumerate().map(|(index, &(time, message))| {
            row()
                .w_full()
                .h(px(26.))
                .flex_shrink_0()
                .gap(px(12.))
                .child(timeline_time(time, dot_alpha(index, history.len())))
                .child(label(message, 16.).min_w_0().truncate())
        }),
    ))
}

const FILES: [(&str, usize, bool); 12] = [
    ("coder-cli", 0, true),
    ("agent", 1, true),
    ("runtime.py", 2, false),
    ("roles.py", 2, false),
    ("context", 1, true),
    ("project.py", 2, false),
    ("storage", 1, true),
    ("sessions.py", 2, false),
    ("tools", 1, true),
    ("web_tools.py", 2, false),
    ("main.py", 1, false),
    ("pyproject.toml", 1, false),
];

fn directory() -> Div {
    let lines = svg()
        .path("icons/tree-connectors.svg")
        .w(px(80.))
        .h(px(336.))
        .text_color(rgb(PRIMARY))
        .absolute()
        .top_0()
        .left_0();
    let entries = FILES.into_iter().map(|(name, depth, folder)| {
        let x = if folder {
            depth as f32 * 28.
        } else {
            depth as f32 * 28. + 12.
        };
        let mut entry = row().h(px(28.)).flex_shrink_0().ml(px(x)).gap(px(12.));
        if folder {
            entry = entry.child(icon("folder-01", 18., MUTED));
        }
        entry.child(muted(name, 14.))
    });
    inspector_section(label("文件目录", 16.))
        .child(column().relative().children(entries).child(lines))
}

fn context() -> Div {
    inspector_section(label("运行上下文", 16.)).child(
        column().gap(px(8.)).pl(px(9.)).children(
            [
                ("模型", "GPT-6.2 Sol 中"),
                ("上下文", "24%"),
                ("今日消耗", "$12"),
            ]
            .into_iter()
            .map(|(title, value)| {
                row()
                    .child(muted(title, 14.).w(px(72.)).flex_shrink_0())
                    .child(muted(value, 14.))
            }),
        ),
    )
}

pub fn status(history: &[(&'static str, &'static str)]) -> Div {
    column()
        .w_full()
        .flex_1()
        .min_h_0()
        .gap(px(20.))
        .child(MaskedScroll::new(
            "status-content",
            64.,
            column()
                .w_full()
                .gap(px(20.))
                .child(timeline(history))
                .child(directory()),
        ))
        .child(context())
}

#[cfg(test)]
mod tests {
    use super::dot_alpha;

    #[test]
    fn timeline_dots_fade_from_latest_to_oldest() {
        assert_eq!(dot_alpha(0, 1), 1.);
        assert_eq!(dot_alpha(0, 2), 0.65);
        assert_eq!(dot_alpha(1, 2), 1.);
        for (index, expected) in [0.65, 0.7375, 0.825, 0.9125, 1.].into_iter().enumerate() {
            assert!((dot_alpha(index, 5) - expected).abs() < f32::EPSILON);
        }
    }
}
