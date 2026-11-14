use crate::{text_fields, theme::*};
use gpui_kit::{
    base::input::{InputContent, Textarea, TextareaState},
    *,
};
use std::hash::{DefaultHasher, Hash, Hasher};

fn content(text: &str) -> (InputContent, String, usize) {
    let mut content = InputContent::new(text.to_owned());
    let mut offset = 0;
    while let Some((start, prefix, folder, closing)) = [
        ("［附件引用：", false, "］"),
        ("［文件引用：", false, "］"),
        ("［目录引用：", true, "］"),
        ("[文件引用：", false, "]"),
        ("[目录引用：", true, "]"),
    ]
    .into_iter()
    .filter_map(|(prefix, folder, closing)| {
        text[offset..]
            .find(prefix)
            .map(|index| (offset + index, prefix, folder, closing))
    })
    .min_by_key(|(index, ..)| *index)
    {
        let json_start = start + prefix.len();
        let mut stream =
            serde_json::Deserializer::from_str(&text[json_start..]).into_iter::<String>();
        let Some(Ok(path)) = stream.next() else {
            offset = json_start;
            continue;
        };
        let json_end = json_start + stream.byte_offset();
        if !text[json_end..].starts_with(closing) {
            offset = json_end;
            continue;
        }
        let end = json_end + closing.len();
        let token = if prefix == "［附件引用：" {
            text_fields::attachment_reference(&path, "历史附件")
        } else {
            text_fields::path_reference(&path, folder)
        };
        // Keep the exact source range so copying and sending preserve the original reference.
        let token = gpui_kit::base::input::InlineToken::new(
            token.id().clone(),
            text[start..end].to_owned(),
        )
        .with_label(token.label().clone());
        if let Ok(next) = content.clone().with_token(start..end, token) {
            content = next;
        }
        offset = end;
    }
    let mut start = 0;
    for word in text.split_inclusive(char::is_whitespace) {
        let source = word.trim_end();
        let end = start + source.len();
        if let Some(name) = source.strip_prefix('/')
            && !name.is_empty()
            && name
                .chars()
                .all(|ch| ch.is_alphanumeric() || matches!(ch, '-' | '_' | ':'))
            && !content.tokens().iter().any(|span| {
                let range = span.range();
                start < range.end && end > range.start
            })
        {
            let glyph = if matches!(name, "super" | "architect" | "executor" | "reviewer") {
                "chat-bot"
            } else {
                "tool-case"
            };
            let token = gpui_kit::base::input::InlineToken::new(
                format!("command:{glyph}:{name}"),
                source.to_owned(),
            )
            .with_label(name.to_owned());
            content = content
                .with_token(start..end, token)
                .expect("command matches its source");
        }
        start += word.len();
    }
    let mut display = String::new();
    offset = 0;
    for span in content.tokens() {
        display.push_str(&text[offset..span.range().start]);
        display.push_str(span.token().label());
        offset = span.range().end;
    }
    display.push_str(&text[offset..]);
    let count = content.tokens().len();
    (content, display, count)
}

pub fn summary(text: &str) -> Div {
    let (content, _, _) = content(text);
    let mut line = row().min_w_0().overflow_hidden();
    let mut offset = 0;
    for span in content.tokens() {
        line = line
            .child(label(text[offset..span.range().start].replace('\n', " "), 14.).flex_shrink_0())
            .child(text_fields::reference_badge(span.token(), false).flex_shrink_0());
        offset = span.range().end;
    }
    line.child(
        label(text[offset..].replace('\n', " "), 14.)
            .min_w_0()
            .truncate(),
    )
}

#[derive(IntoElement)]
pub struct MessageReferences {
    pub id: SharedString,
    pub text: SharedString,
    pub max_width: f32,
}

impl RenderOnce for MessageReferences {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let (content, display, count) = content(&self.text);
        if count == 0 {
            return label(self.text, 14.).into_any_element();
        }
        let measure = |text: &str, window: &mut Window, cx: &mut App| {
            label(text.to_owned(), 14.)
                .font(body_font())
                .into_any_element()
                .layout_as_root(
                    size(AvailableSpace::MaxContent, AvailableSpace::MaxContent),
                    window,
                    cx,
                )
                .width
        };
        let token_padding: Pixels = content
            .tokens()
            .iter()
            .map(|span| {
                text_fields::reference_badge(span.token(), false)
                    .font(body_font())
                    .into_any_element()
                    .layout_as_root(
                        size(AvailableSpace::MaxContent, AvailableSpace::MaxContent),
                        window,
                        cx,
                    )
                    .width
                    - measure(span.token().label(), window, cx)
            })
            .sum();
        let mut hash = DefaultHasher::new();
        self.id.hash(&mut hash);
        self.text.hash(&mut hash);
        let state = window.use_keyed_state(
            SharedString::from(format!("message-reference-{}", hash.finish())),
            cx,
            |window, cx| {
                let mut state = TextareaState::new(window, cx).auto_grow(1, usize::MAX);
                state.set_value(content, window, cx);
                state.set_editor_style(text_fields::editor_style());
                state.set_readonly(true, cx);
                state
            },
        );
        let width = display
            .lines()
            .map(|line| {
                window
                    .text_system()
                    .shape_line(
                        line.to_owned().into(),
                        px(14.),
                        &[TextRun {
                            len: line.len(),
                            font: body_font(),
                            color: rgb(INK).into(),
                            background_color: None,
                            underline: None,
                            strikethrough: None,
                        }],
                        None,
                    )
                    .width
            })
            .max()
            .unwrap_or(px(0.));
        div()
            // GPUI's textarea reserves 10 px at the right edge for the caret.
            .w((width + token_padding + px(12.)).min(px(self.max_width.max(1.))))
            .font(body_font())
            .text_size(px(14.))
            .line_height(px(24.))
            .child(Textarea::new(&state).token(|token, _, _| text_fields::reference_label(token)))
            .into_any_element()
    }
}

#[cfg(test)]
mod tests {
    use super::content;
    #[test]
    fn references_restore_tokens_without_changing_source_text() {
        let text = "请看［文件引用：\"/project/文件.rs\"］和［目录引用：\"/project/src\"］";
        let (content, display, count) = content(text);
        assert_eq!(count, 2);
        assert_eq!(content.tokens().len(), 2);
        assert_eq!(display, "请看文件.rs和src");
        assert_eq!(
            content.tokens()[0].token().text().as_ref(),
            "［文件引用：\"/project/文件.rs\"］"
        );
        assert_eq!(super::content("［文件引用：invalid］").2, 0);
        assert_eq!(super::content("ordinary text").1, "ordinary text");
    }

    #[test]
    fn commands_and_paths_share_badges_and_preserve_source_ranges() {
        let text = "/executor 看［文件引用：\"/project/a b.rs\"］ /review-code";
        let (content, display, count) = content(text);
        assert_eq!(count, 3);
        assert_eq!(display, "executor 看a b.rs review-code");
        for span in content.tokens() {
            assert_eq!(&text[span.range()], span.token().text().as_ref());
        }
        assert_eq!(
            content.tokens()[0].token().id().as_ref(),
            "command:chat-bot:executor"
        );
        assert_eq!(
            content.tokens()[2].token().id().as_ref(),
            "command:tool-case:review-code"
        );
        for literal in [
            r"\/executor",
            "https://example.com/a",
            "/project/src",
            "text/executor",
        ] {
            assert_eq!(super::content(literal).2, 0, "{literal}");
        }
    }
}

#[cfg(all(test, feature = "visual-check"))]
mod layout_tests {
    use super::{MessageReferences, summary};
    use crate::theme::column;
    use gpui_kit::{
        AppContext, Context, InteractiveElement, IntoElement, Modifiers, ParentElement, Render,
        Styled, TestAppContext, TestDispatcher, Window, px,
    };
    use gpui_kit::{base::Button, component::Root};

    struct Host(bool);
    impl Render for Host {
        fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
            column().w(px(220.)).children([
                MessageReferences {
                    id: "badge-message".into(),
                    text: "/executor 所有修改写commit".into(),
                    max_width: 220.,
                }
                .into_any_element(),
                Button::new("timeline-row")
                    .debug_selector(|| "timeline-row".into())
                    .w_full()
                    .child(summary(
                        "/executor 所有修改写commit［文件引用：\"/project/main.rs\"］",
                    ))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.0 = true;
                        cx.notify();
                    }))
                    .into_any_element(),
            ])
        }
    }

    #[test]
    fn badge_timeline_stays_single_line_and_clickable() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| gpui_kit::init(cx));
        let mut host = None;
        let (_, window) = cx.add_window_view(|window, cx| {
            let view = cx.new(|_| Host(false));
            host = Some(view.clone());
            Root::new(view, window, cx)
        });
        window.run_until_parked();
        window.update(|window, cx| {
            window.draw(cx).clear(cx);
        });
        let bounds = window.debug_bounds("timeline-row").unwrap();
        assert!(bounds.size.width <= px(220.));
        assert!(bounds.size.height <= px(40.));
        window.simulate_click(bounds.center(), Modifiers::default());
        window.update(|_, cx| assert!(host.unwrap().read(cx).0));
    }

    struct Bubble;
    impl Render for Bubble {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            column().w(px(600.)).px(px(30.)).child(
                crate::chat::user_message("wrap-check", "/executor 所有修改写commit", 600.)
                    .debug_selector(|| "short-user-message".into()),
            )
        }
    }

    #[test]
    fn short_badge_message_does_not_wrap_early() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| gpui_kit::init(cx));
        let (_, window) = cx.add_window_view(|window, cx| {
            let view = cx.new(|_| Bubble);
            Root::new(view, window, cx)
        });
        window.run_until_parked();
        window.update(|window, cx| {
            window.draw(cx).clear(cx);
        });
        assert_eq!(
            window
                .debug_bounds("short-user-message")
                .unwrap()
                .size
                .height,
            px(46.)
        );
    }
}
