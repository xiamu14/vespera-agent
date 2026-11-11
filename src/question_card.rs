use crate::{Vespera, live_state::Question, theme::*};
use gpui_kit::{
    base::{Button, input::InputState},
    *,
};

pub fn control(multiple: bool, selected: bool) -> Div {
    let frame = div()
        .size(px(16.))
        .flex_shrink_0()
        .flex()
        .items_center()
        .justify_center()
        .border_1()
        .border_color(rgb(if selected { PRIMARY } else { 0xdcdcdf }));
    let frame = if multiple {
        frame.rounded(px(4.))
    } else {
        frame.rounded_full()
    };
    if selected && multiple {
        frame
            .bg(rgb(PRIMARY))
            .child(label("✓", 12.).text_color(rgb(0xffffff)))
    } else if selected {
        frame.child(div().size(px(8.)).rounded_full().bg(rgb(PRIMARY)))
    } else {
        frame
    }
}

#[derive(IntoElement)]
pub struct QuestionCard {
    pub question: Question,
    pub conversation_id: String,
    pub answering: bool,
    pub app: WeakEntity<Vespera>,
}

impl RenderOnce for QuestionCard {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let question = self.question;
        let token = question.id;
        let input = window.use_keyed_state(
            SharedString::from(format!("question-input-{}-{token}", self.conversation_id)),
            cx,
            |window, cx| {
                let mut state = InputState::new(window, cx).placeholder("其他意见");
                state.set_editor_style(crate::text_fields::editor_style());
                state
            },
        );
        let mut content = chat_card()
            .p(px(8.))
            .gap(px(8.))
            .child(
                row()
                    .gap(px(8.))
                    .flex_wrap()
                    .child(label(question.header.clone(), 14.).font(emphasis_font()))
                    .child(muted(
                        if question.multi_select {
                            "（可选择多项）"
                        } else {
                            "（仅可选择一项）"
                        },
                        12.,
                    )),
            )
            .children((!question.details.is_empty()).then(|| muted(question.details.clone(), 14.)))
            .child(muted(question.question.clone(), 14.));
        for (index, option) in question.options.iter().enumerate() {
            let selected = question.selected.contains(&option.label);
            let app = self.app.clone();
            let id = self.conversation_id.clone();
            let choice = option.label.clone();
            content = content.child(
                Button::new(SharedString::from(format!("question-{token}-{index}")))
                    .accessibility_label(choice.clone())
                    .disabled(self.answering)
                    .w_full()
                    .justify_start()
                    .gap(px(10.))
                    .px(px(10.))
                    .py(px(8.))
                    .rounded(px(DETAIL_RADIUS))
                    .bg(rgb(FIELD))
                    .child(control(question.multi_select, selected))
                    .child(
                        column()
                            .flex_1()
                            .min_w_0()
                            .child(label(option.label.clone(), 14.))
                            .children(
                                option
                                    .description
                                    .clone()
                                    .filter(|text| !text.is_empty())
                                    .map(|text| muted(text, 12.)),
                            ),
                    )
                    .on_click(move |_, _, cx| {
                        let _ = app.update(cx, |app, cx| {
                            app.choose_answer(id.clone(), token, choice.clone(), cx)
                        });
                    }),
            );
        }
        let app = self.app.clone();
        let id = self.conversation_id.clone();
        let custom = input.clone();
        content = content.child(
            row()
                .gap(px(8.))
                .rounded(px(CONTROL_RADIUS))
                .bg(rgb(FIELD))
                .px(px(10.))
                .py(px(6.))
                .child(crate::text_fields::input_field(&input))
                .child(
                    Button::new("send-question-opinion")
                        .accessibility_label("发送其他意见")
                        .disabled(self.answering)
                        .size(px(28.))
                        .rounded_full()
                        .bg(rgb(PRIMARY_WASH))
                        .child(icon("arrow-up-02", 18., PRIMARY))
                        .on_click(move |_, _, cx| {
                            let value = custom.read(cx).value().trim().to_owned();
                            if !value.is_empty() {
                                let _ = app.update(cx, |app, cx| {
                                    app.reply_to_question(
                                        id.clone(),
                                        token,
                                        vec![],
                                        Some(value),
                                        cx,
                                    )
                                });
                            }
                        }),
                ),
        );
        if question.multi_select {
            let selected = question.selected;
            let app = self.app;
            let id = self.conversation_id;
            content = content.child(
                row().justify_end().child(
                    Button::new("confirm-question")
                        .accessibility_label("确认选项")
                        .disabled(selected.is_empty() || self.answering)
                        .px(px(10.))
                        .py(px(6.))
                        .rounded(px(DETAIL_RADIUS))
                        .bg(rgb(PRIMARY_WASH))
                        .child(label("确定", 14.).text_color(rgb(PRIMARY)))
                        .on_click(move |_, _, cx| {
                            let _ = app.update(cx, |app, cx| {
                                app.reply_to_question(id.clone(), token, selected.clone(), None, cx)
                            });
                        }),
                ),
            );
        }
        content
    }
}

#[cfg(all(test, feature = "visual-check"))]
mod tests {
    use super::{Question, QuestionCard};
    use crate::Vespera;
    use gpui_kit::{
        AppContext, Context, IntoElement, ParentElement, Render, Styled, TestAppContext,
        TestDispatcher, Window, component::Root, div, init,
    };

    struct Host {
        app: gpui_kit::Entity<Vespera>,
        multiple: bool,
    }

    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            let question: Question = serde_json::from_value(serde_json::json!({
                "header":"确认需求", "question":"选择处理方式", "multi_select":self.multiple,
                "options":[{"label":"A","description":"第一项"},{"label":"B"}]
            }))
            .unwrap();
            div().w(gpui_kit::px(420.)).child(QuestionCard {
                question,
                conversation_id: "test".into(),
                answering: false,
                app: self.app.downgrade(),
            })
        }
    }

    #[test]
    fn both_choice_types_have_custom_opinion_input_and_send() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| init(cx));
        for multiple in [false, true] {
            let (_, window) = cx.add_window_view(|window, cx| {
                let app = cx.new(|_| Vespera::default());
                let host = cx.new(|_| Host { app, multiple });
                Root::new(host, window, cx)
            });
            window.run_until_parked();
            window.update(|window, cx| {
                window.draw(cx).clear(cx);
                assert!(
                    gpui_kit::base::test_support::find(
                        window,
                        &[],
                        &"send-question-opinion".into()
                    )
                    .is_some()
                );
                assert!(
                    gpui_kit::base::test_support::find(window, &[], &"question-0-0".into())
                        .is_some()
                );
                assert_eq!(
                    gpui_kit::base::test_support::find(window, &[], &"confirm-question".into())
                        .is_some(),
                    multiple
                );
            });
        }
    }
}
