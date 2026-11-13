use crate::theme::*;
use gpui_kit::base::Button;
use gpui_kit::base::text::{CodeBlock, TextView, TextViewStyle};
use gpui_kit::*;

const CODE_INSET: f32 = 12.;

pub fn code_header(block: &CodeBlock, width: Pixels, window: &Window) -> Div {
    let code = block.code();
    // shortcut: the action slot is right-aligned, use the chat content width for full-width blocks.
    row()
        .w((width - px(CODE_INSET + 1.) - window.rem_size() / 2.).max(px(0.)))
        .justify_between()
        .child(muted(block.lang().unwrap_or_default(), 14.).font(body_font()))
        .child(CopyCode { code })
}

#[derive(IntoElement)]
struct CopyCode {
    code: SharedString,
}

impl RenderOnce for CopyCode {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state("copy-feedback", cx, |_, _| false);
        let copied = *state.read(cx);
        Button::new("copy-code")
            .accessibility_label(if copied { "已复制" } else { "复制代码" })
            .disabled(copied)
            .size(px(26.))
            .flex()
            .items_center()
            .justify_center()
            .rounded(px(DETAIL_RADIUS))
            .child(if copied {
                icon("check", 16., 0x27976c)
            } else {
                icon("copy-01", 16., MUTED)
            })
            .on_click(move |_, _, cx| {
                if *state.read(cx) {
                    return;
                }
                cx.write_to_clipboard(ClipboardItem::new_string(self.code.to_string()));
                state.update(cx, |copied, cx| {
                    *copied = true;
                    cx.notify();
                });
                let reset = state.downgrade();
                let timer = cx
                    .background_executor()
                    .timer(std::time::Duration::from_millis(500));
                cx.spawn(async move |cx| {
                    timer.await;
                    let _ = reset.update(cx, |copied, cx| {
                        *copied = false;
                        cx.notify();
                    });
                })
                .detach();
            })
    }
}

pub fn markdown_surface(view: TextView) -> Div {
    div()
        .relative()
        .w_full()
        .min_w_0()
        .text_size(px(15.))
        .child(view)
        // GPUI renders links with a hand cursor inside its Markdown element.
        .child(div().absolute().inset_0().cursor_default())
}

pub fn markdown(id: impl Into<ElementId>, source: impl Into<SharedString>) -> TextView {
    let style = TextViewStyle::default()
        .with_foreground(rgb(INK).into())
        .with_muted_foreground(rgb(MUTED).into())
        .with_link(rgb(PRIMARY).into())
        .with_border(rgb(BORDER).into())
        .with_code_background(rgb(FIELD).into())
        .with_paragraph_gap(rems(0.5))
        .with_table(
            StyleRefinement::default()
                .rounded(px(CONTROL_RADIUS))
                .overflow_hidden(),
        )
        .with_inline_code_style(
            StyleRefinement::default()
                .font(code_font())
                .text_size(px(14.)),
        )
        .with_heading(|level| {
            StyleRefinement::default()
                .text_size(px(if level == 1 { 18. } else { 16. }))
                .font(emphasis_font())
                .pt(px(0.))
                .pb(px(8.))
                .mt(px(0.))
                .mb(px(0.))
        })
        .with_list_style(StyleRefinement::default().text_color(rgb(PRIMARY)))
        .with_blockquote_style(
            StyleRefinement::default()
                .border_0()
                .px(px(8.))
                .py(px(4.))
                .rounded(px(DETAIL_RADIUS))
                .bg(rgb(FIELD))
                .text_color(rgb(MUTED)),
        )
        .with_code_block(
            StyleRefinement::default()
                .font(code_font())
                .text_size(px(14.))
                .p(px(CODE_INSET))
                .pt(px(36.))
                .rounded(px(CONTROL_RADIUS))
                .bg(rgb(FIELD)),
        );
    TextView::markdown(id, source).style(style).selectable(true)
}

#[cfg(all(test, feature = "visual-check"))]
mod tests {
    use super::{markdown, markdown_surface};
    use gpui_kit::{
        AppContext, Context, InteractiveElement, IntoElement, ParentElement, Render, Styled,
        TestAppContext, TestDispatcher, Window, component::Root, div, init, px,
    };

    struct Host;
    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            div().w(px(200.)).child(
                markdown_surface(markdown("wrapping", "总结：**这份配置采用统一格式加少量明确检查的策略，而不是启用大量严格规则。这段加粗文本应该自动换行并完整显示。**"))
                    .id("paragraph")
                    .debug_selector(|| "paragraph".into()),
            )
        }
    }

    #[test]
    fn long_strong_text_wraps_within_chat_width() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| init(cx));
        let (_, window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| Host);
            Root::new(host, window, cx)
        });
        for _ in 0..4 {
            window.run_until_parked();
            window.update(|window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            });
        }
        let bounds = window.debug_bounds("paragraph").unwrap();
        assert_eq!(bounds.size.width, px(200.));
        assert!(
            bounds.size.height > px(60.),
            "long emphasis must occupy multiple lines: {bounds:?}"
        );
    }

    struct CopyHost;
    impl Render for CopyHost {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            div().child(super::CopyCode {
                code: "copy me".into(),
            })
        }
    }

    #[test]
    fn copy_feedback_disables_for_half_a_second_then_allows_reuse() {
        use gpui_kit::{Modifiers, VisualTestContext, base::test_support::find};
        use std::time::Duration;
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| init(cx));
        let executor = cx.background_executor.clone();
        let (_, window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| CopyHost);
            Root::new(host, window, cx)
        });
        fn draw(window: &mut VisualTestContext) {
            window.run_until_parked();
            window.update(|window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            });
        }
        fn copied(window: &mut VisualTestContext) -> bool {
            window.update(|window, _| {
                find(window, &[], &"copy-code".into()).unwrap().label() == Some("已复制")
            })
        }
        draw(window);
        let center = window.update(|window, _| {
            find(window, &[], &"copy-code".into())
                .unwrap()
                .bounds()
                .center()
        });
        assert!(!copied(window));
        window.simulate_click(center, Modifiers::default());
        draw(window);
        assert!(copied(window));
        executor.advance_clock(Duration::from_millis(499));
        draw(window);
        assert!(copied(window));
        executor.advance_clock(Duration::from_millis(1));
        draw(window);
        assert!(!copied(window));
        window.simulate_click(center, Modifiers::default());
        draw(window);
        assert!(copied(window));
    }
}
