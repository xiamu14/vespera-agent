use std::rc::Rc;

use crate::{Vespera, system_notification::notify, theme::*};
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use gpui_kit::{base::Button, component::WindowExt};

#[derive(Default)]
struct Directory {
    path: Option<String>,
    picking: bool,
}

pub fn open(app: &Vespera, window: &mut Window, cx: &mut Context<Vespera>) {
    if app.chats.as_ref().is_some_and(|chats| !chats.can_create()) || window.has_active_dialog(cx) {
        return;
    }
    let app = cx.entity().downgrade();
    let directory = cx.new(|_| Directory::default());
    window.open_dialog(cx, move |dialog, _, cx| {
        let selected = directory.read(cx);
        let path = selected.path.clone();
        let has_path = path.is_some();
        let picking = selected.picking;
        let draft = directory.clone();
        let clear = directory.clone();
        let confirm = {
            let app = app.clone();
            let draft = directory.clone();
            Rc::new(move |cx: &mut App| {
                let draft = draft.read(cx);
                if draft.picking {
                    return false;
                }
                let path = draft.path.clone();
                app.update(cx, |app, cx| app.chats.is_none() || app.new_chat(path, cx))
                    .unwrap_or(false)
            })
        };
        let submit = confirm.clone();
        dialog
            .centered()
            .w(px(377.))
            .p(px(16.))
            .rounded(px(PANEL_RADIUS))
            .bg(rgb(BACKGROUND))
            .close_button(false)
            .on_ok(move |_, _, cx| confirm(cx))
            .child(
                column()
                    .gap(px(14.))
                    .font(body_font())
                    .child(label("添加工作目录", 20.).font(emphasis_font()))
                    .child(
                        row()
                            .h(px(if has_path { 40. } else { 90. }))
                            .px(px(10.))
                            .gap(px(8.))
                            .rounded(px(CONTROL_RADIUS))
                            .bg(rgb(FIELD))
                            .child(
                                Button::new("choose-work-directory")
                                    .accessibility_label("选择工作目录（可选）")
                                    .disabled(picking)
                                    .flex()
                                    .items_center()
                                    .flex_1()
                                    .min_w_0()
                                    .h_full()
                                    .gap(px(8.))
                                    .when(has_path, |button| button.justify_start())
                                    .when(!has_path, |button| button.justify_center())
                                    .children((!has_path).then(|| icon("folder-01", 18., MUTED)))
                                    .child(
                                        muted(
                                            path.clone()
                                                .unwrap_or_else(|| "选择目录（可选）".into()),
                                            14.,
                                        )
                                        .min_w_0()
                                        .whitespace_nowrap()
                                        .overflow_hidden(),
                                    )
                                    .on_click(move |_, _, cx| choose(&draft, cx)),
                            )
                            .children(path.map(|_| {
                                Button::new("clear-work-directory")
                                    .accessibility_label("清除所选工作目录")
                                    .disabled(picking)
                                    .size(px(28.))
                                    .flex_shrink_0()
                                    .flex()
                                    .items_center()
                                    .justify_center()
                                    .child(icon("delete-02", 18., 0xff626c))
                                    .on_click(move |_, _, cx| {
                                        clear.update(cx, |draft, cx| {
                                            if !draft.picking {
                                                draft.path = None;
                                                cx.notify();
                                            }
                                        })
                                    })
                            })),
                    )
                    .child(
                        row()
                            .justify_end()
                            .gap(px(12.))
                            .child(
                                action("discard-new-chat", "取消", false)
                                    .on_click(|_, window, cx| window.close_dialog(cx)),
                            )
                            .child(
                                action("confirm-new-chat", "确定", true)
                                    .disabled(picking)
                                    .opacity(if picking { 0.5 } else { 1. })
                                    .on_click(move |_, window, cx| {
                                        if submit(cx) {
                                            window.close_dialog(cx);
                                        }
                                    }),
                            ),
                    ),
            )
    });
}

fn action(id: &'static str, title: &'static str, primary: bool) -> Button {
    Button::new(id)
        .accessibility_label(title)
        .flex()
        .items_center()
        .h(px(36.))
        .min_w(px(if primary { 72. } else { 68. }))
        .justify_center()
        .rounded(px(CONTROL_RADIUS))
        .border_1()
        .border_color(rgb(if primary { PRIMARY } else { BORDER }))
        .bg(rgb(if primary { PRIMARY_WASH } else { BACKGROUND }))
        .text_color(rgb(if primary { PRIMARY } else { INK }))
        .child(label(title, 14.))
}

fn choose(directory: &Entity<Directory>, cx: &mut App) {
    if directory.read(cx).picking {
        return;
    }
    directory.update(cx, |draft, cx| {
        draft.picking = true;
        cx.notify();
    });
    let picked = cx.prompt_for_paths(PathPromptOptions {
        files: false,
        directories: true,
        multiple: false,
        prompt: Some("选择工作目录".into()),
    });
    let directory = directory.downgrade();
    cx.spawn(async move |cx| {
        let picked = picked.await;
        let _ = directory.update(cx, |draft, cx| {
            draft.picking = false;
            match picked {
                Ok(Ok(Some(paths))) => {
                    if let Some(path) = paths.first() {
                        if let Some(path) = path.to_str() {
                            draft.path = Some(path.to_owned());
                        } else {
                            notify("无法选择工作目录", "此路径包含无法保存的字符。");
                        }
                    }
                }
                Ok(Ok(None)) => {}
                _ => notify("无法打开目录选择器", "请重试。"),
            }
            cx.notify();
        });
    })
    .detach();
}

#[cfg(all(test, feature = "visual-check"))]
mod tests {
    use crate::Vespera;
    use gpui_kit::{
        AppContext, Modifiers, Pixels, Point, TestAppContext, TestDispatcher, VisualTestContext,
        component::WindowExt, init, px, size,
    };

    #[test]
    fn modal_allows_directory_selection_clear_and_cancel() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| {
            init(cx);
            cx.set_reduce_motion(true);
        });
        let (_, window) = cx.add_window_view(|window, cx| {
            let app = cx.new(|_| Vespera::default());
            gpui_kit::component::Root::new(app, window, cx)
        });

        fn draw(window: &mut VisualTestContext) {
            window.run_until_parked();
            window.update(|window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            });
        }

        fn button(window: &mut VisualTestContext, id: &'static str) -> Option<Point<Pixels>> {
            window.update(|window, _| {
                gpui_kit::base::test_support::find(window, &[], &id.into())
                    .map(|element| element.bounds().center())
            })
        }

        window.simulate_resize(size(px(1440.), px(960.)));
        draw(window);
        let new_chat = window.debug_bounds("new-chat").unwrap().center();
        window.simulate_click(new_chat, Modifiers::default());
        draw(window);
        assert!(window.update(|window, cx| window.has_active_dialog(cx)));
        let picker = button(window, "choose-work-directory").unwrap();
        window.simulate_click(picker, Modifiers::default());
        window.simulate_path_prompt_response(|options| {
            assert!(options.directories && !options.files && !options.multiple);
            Some(vec!["/tmp/工作目录".into()])
        });
        draw(window);
        let clear = button(window, "clear-work-directory").unwrap();
        window.simulate_click(clear, Modifiers::default());
        draw(window);
        assert!(button(window, "clear-work-directory").is_none());
        let confirm = button(window, "confirm-new-chat").unwrap();
        window.simulate_click(confirm, Modifiers::default());
        draw(window);
        assert!(!window.update(|window, cx| window.has_active_dialog(cx)));

        window.simulate_click(new_chat, Modifiers::default());
        draw(window);
        let cancel = button(window, "discard-new-chat").unwrap();
        window.simulate_click(cancel, Modifiers::default());
        assert!(!window.update(|window, cx| window.has_active_dialog(cx)));
    }
}
