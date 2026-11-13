use crate::masked_scroll::MaskedScroll;
use crate::theme::*;
use gpui_kit::{base::Progress, *};

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

fn context_section(model: impl Into<SharedString>, used: Option<f64>, cost: Option<f64>) -> Div {
    let fraction = used
        .filter(|value| value.is_finite())
        .map(|value| value.clamp(0., 1.));
    inspector_section(label("运行状态", 16.)).child(
        column()
            .gap(px(8.))
            .pl(px(9.))
            .child(
                row()
                    .child(muted("模型", 14.).w(px(100.)).flex_shrink_0())
                    .child(muted(model, 14.).min_w_0().truncate()),
            )
            .child(
                row()
                    .child(muted("上下文", 14.).w(px(100.)).flex_shrink_0())
                    .child(
                        Progress::new("context-progress")
                            .accessibility_label("上下文使用量")
                            .value((fraction.unwrap_or(0.) * 100.) as f32)
                            .indeterminate(fraction.is_none())
                            .w(px(80.))
                            .h(px(4.))
                            .flex_shrink_0()
                            .rounded_full()
                            .overflow_hidden()
                            .bg(rgb(PROGRESS_TRACK))
                            .child(
                                div()
                                    .h_full()
                                    .w(relative(fraction.unwrap_or(0.) as f32))
                                    .rounded_full()
                                    .bg(rgb(PRIMARY)),
                            ),
                    ),
            )
            .child(
                row()
                    .child(muted("费用（总计）", 14.).w(px(100.)).flex_shrink_0())
                    .child(match cost {
                        Some(cost) => {
                            let mut amount_font = body_font();
                            amount_font.weight = FontWeight::SEMIBOLD;
                            row()
                                .gap(px(4.))
                                .child(muted("$", 14.).font(emphasis_font()))
                                .child(muted(format!("{cost:.2}"), 16.).font(amount_font))
                        }
                        None => muted("—", 14.),
                    }),
            ),
    )
}

fn context() -> Div {
    context_section("GPT-6.1 Sol", Some(0.24), Some(12.))
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

fn model_display_name(id: &str) -> &str {
    match id
        .rsplit('/')
        .next()
        .unwrap_or(id)
        .to_ascii_lowercase()
        .as_str()
    {
        "gpt-6.1-sol" => "GPT-6.1 Sol",
        "gpt-6-astra" => "GPT-6 Astra",
        "gpt-6.1-terra" => "GPT-6.1 Terra",
        "gpt-6-sol" => "GPT-6 Sol",
        "gpt-6-luna" => "GPT-6 Luna",
        "gpt-5.6-sol" => "GPT-5.6 Sol",
        "gpt-5.6-terra" => "GPT-5.6 Terra",
        "gpt-5.6-luna" => "GPT-5.6 Luna",
        "claude-opus-5.5" | "claude-opus-5-5" => "Claude Opus 5.5",
        "claude-opus-5" => "Claude Opus 5",
        "claude-opus-4.8" | "claude-opus-4-8" => "Claude Opus 4.8",
        "claude-opus-4.7" | "claude-opus-4-7" => "Claude Opus 4.7",
        "claude-opus-4.6" | "claude-opus-4-6" => "Claude Opus 4.6",
        "claude-opus-4.5" | "claude-opus-4-5" | "claude-opus-4-5-20251101" => "Claude Opus 4.5",
        "claude-sonnet-5.5" | "claude-sonnet-5-5" => "Claude Sonnet 5.5",
        "claude-sonnet-5" => "Claude Sonnet 5",
        "claude-sonnet-4.6" | "claude-sonnet-4-6" => "Claude Sonnet 4.6",
        "claude-sonnet-4.5" | "claude-sonnet-4-5" | "claude-sonnet-4-5-20250929" => {
            "Claude Sonnet 4.5"
        }
        "claude-haiku-5.5" | "claude-haiku-5-5" => "Claude Haiku 5.5",
        "claude-haiku-4.5" | "claude-haiku-4-5" | "claude-haiku-4-5-20251001" => "Claude Haiku 4.5",
        "claude-fable-5.2" | "claude-fable-5-2" => "Claude Fable 5.2",
        "claude-fable-5.1" | "claude-fable-5-1" => "Claude Fable 5.1",
        "claude-fable-5" => "Claude Fable 5",
        "claude-mythos-5.2" | "claude-mythos-5-2" => "Claude Mythos 5.2",
        "claude-mythos-5.1" | "claude-mythos-5-1" => "Claude Mythos 5.1",
        "claude-mythos-5" => "Claude Mythos 5",
        "minimax-m3.1-flash" => "MiniMax M3.1 Flash",
        "minimax-m3" => "MiniMax M3",
        "minimax-m2.7" => "MiniMax M2.7",
        "minimax-m2.7-highspeed" => "MiniMax M2.7 Highspeed",
        "minimax-m2.5" => "MiniMax M2.5",
        "minimax-m2.5-lightning" => "MiniMax M2.5 Lightning",
        "minimax-m2.1" => "MiniMax M2.1",
        "minimax-m2" => "MiniMax M2",
        "minimax-m2-her" => "MiniMax M2 Her",
        "mimo-v2.6-pro" => "MiMo-V2.6-Pro",
        "mimo-v2.6-flash" => "MiMo-V2.6-Flash",
        "mimo-v2.5" => "MiMo-V2.5",
        "mimo-v2.5-pro" => "MiMo-V2.5-Pro",
        "mimo-v2.5-pro-ultraspeed" => "MiMo-V2.5-Pro-UltraSpeed",
        "mimo-v2-pro" => "MiMo-V2-Pro",
        "mimo-v2-flash" => "MiMo-V2-Flash",
        "mimo-v2-omni" => "MiMo-V2-Omni",
        "glm-5.3" => "GLM-5.3",
        "glm-5.3-flash" => "GLM-5.3-Flash",
        "glm-5.3-flash-event" => "GLM-5.3-Flash Event",
        "glm-5.3-flashx" => "GLM-5.3-FlashX",
        "glm-5.2" => "GLM-5.2",
        "glm-5.1" => "GLM-5.1",
        "glm-5" => "GLM-5",
        "glm-4.7" => "GLM-4.7",
        "glm-4.7-flash" => "GLM-4.7-Flash",
        "gemini-3.5-flash" => "Gemini 3.5 Flash",
        "gemini-3.1-pro-preview" => "Gemini 3.1 Pro Preview",
        "deepseek-v4-pro" => "DeepSeek-V4-Pro",
        "deepseek-v4-flash" => "DeepSeek-V4-Flash",
        "deepseek-v4.1-flash" => "DeepSeek-V4.1-Flash",
        "kimi-k2.6" => "Kimi K2.6",
        "qwen3.7-max" => "Qwen3.7-Max",
        _ => id,
    }
}

#[cfg(test)]
mod tests {
    use super::{dot_alpha, model_display_name};

    #[test]
    fn model_ids_use_public_names_and_preserve_unknown_models() {
        for (id, expected) in [
            ("gpt-6.1-sol", "GPT-6.1 Sol"),
            ("gpt-6-astra", "GPT-6 Astra"),
            ("gpt-6.1-terra", "GPT-6.1 Terra"),
            ("gpt-6-sol", "GPT-6 Sol"),
            ("gpt-6-luna", "GPT-6 Luna"),
            ("gpt-5.6-sol", "GPT-5.6 Sol"),
            ("gpt-5.6-terra", "GPT-5.6 Terra"),
            ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ] {
            assert_eq!(model_display_name(id), expected);
            assert_eq!(model_display_name(&format!("openai/{id}")), expected);
        }
        for (id, expected) in [
            ("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
            ("claude-opus-5-5", "Claude Opus 5.5"),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("minimax/minimax-m3.1-flash", "MiniMax M3.1 Flash"),
            ("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed"),
            ("xiaomi/mimo-v2.6-pro", "MiMo-V2.6-Pro"),
            ("mimo-v2.5-pro", "MiMo-V2.5-Pro"),
            ("GLM-5.3-Flash-Event", "GLM-5.3-Flash Event"),
            ("z-ai/glm-5.3-flash", "GLM-5.3-Flash"),
            ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            ("deepseek/deepseek-v4-pro", "DeepSeek-V4-Pro"),
            ("moonshotai/kimi-k2.6", "Kimi K2.6"),
            ("qwen/qwen3.7-max", "Qwen3.7-Max"),
        ] {
            assert_eq!(model_display_name(id), expected);
        }
        assert_eq!(model_display_name("custom/model"), "custom/model");
        assert_eq!(model_display_name("—"), "—");
    }

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

pub fn live(
    data: Option<&crate::inspector::Inspection>,
    app: &crate::Vespera,
    cx: &Context<crate::Vespera>,
) -> Div {
    let turn = app.selected_turn();
    let directory = data
        .map(|data| data.directory.as_slice())
        .unwrap_or_default();
    let model = turn
        .and_then(|turn| turn.model.as_deref())
        .or_else(|| data.and_then(|data| data.model.as_deref()))
        .map(model_display_name)
        .unwrap_or("—")
        .to_owned();
    let used = turn
        .and_then(|turn| turn.context_used)
        .or_else(|| data.and_then(|data| data.context_used));
    let cost = data.and_then(|data| data.total_cost);
    let recent = crate::chat_timeline::render(app, cx);
    let files = inspector_section(label("文件目录", 16.))
        .children(
            app.chats
                .as_ref()
                .and_then(|chats| chats.history.visible())
                .filter(|_| !directory.is_empty())
                .and_then(|chat| chat.work_path.as_ref().map(|path| (chat, path)))
                .map(|(chat, path)| crate::file_tree::FileTree {
                    root: path.into(),
                    conversation_id: chat.conversation_id.clone(),
                    entries: directory.to_vec(),
                    app: cx.entity().downgrade(),
                }),
        )
        .children(
            data.is_some_and(|data| data.directory_truncated)
                .then(|| muted("目录内容已截断", 12.)),
        );
    let context = context_section(model, used, cost);
    column()
        .w_full()
        .flex_1()
        .min_h_0()
        .gap(px(20.))
        .child(MaskedScroll::new(
            "status-content",
            64.,
            column().w_full().gap(px(20.)).child(recent).child(files),
        ))
        .child(context)
}

#[cfg(all(test, feature = "visual-check"))]
mod layout_tests {
    use super::context_section;
    use gpui_kit::{
        AppContext, Context, IntoElement, ParentElement, Render, Styled, TestAppContext,
        TestDispatcher, Window, base::test_support::find, component::Root, div, init, px,
    };

    struct Host;
    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            div()
                .w(px(340.))
                .child(context_section("GPT-6.1 Sol", Some(0.05), Some(0.22)))
        }
    }

    #[test]
    fn context_progress_is_eighty_pixels_wide() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| init(cx));
        let (_, window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| Host);
            Root::new(host, window, cx)
        });
        window.run_until_parked();
        window.update(|window, cx| {
            window.draw(cx).clear(cx);
            let bar = find(window, &[], &"context-progress".into()).unwrap();
            assert_eq!(bar.bounds().size.width, px(80.));
            assert_eq!(bar.bounds().size.height, px(4.));
        });
    }
}
