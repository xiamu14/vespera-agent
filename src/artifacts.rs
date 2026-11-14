use crate::{fixtures, theme::*};
use gpui_kit::*;

fn section(time: &'static str) -> Div {
    card().p(px(14.)).gap(px(12.)).child(
        row()
            .gap(px(12.))
            .w(relative(0.5))
            .h(px(26.))
            .overflow_hidden()
            .child(timeline_time(time, 1.))
            .child(label(fixtures::QUESTION, 16.).min_w_0().truncate()),
    )
}

fn diff() -> Div {
    let removed = label("− message_history = []", 14.)
        .font(code_font())
        .line_through()
        .px(px(8.))
        .h(px(26.))
        .rounded(px(DETAIL_RADIUS))
        .bg(rgb(0xfcf1f8));
    let added = label("+ message_history = restore()", 14.)
        .font(code_font())
        .px(px(8.))
        .h(px(26.))
        .rounded(px(DETAIL_RADIUS))
        .bg(rgb(0xd2f8e3));
    section("18:28").h(px(156.)).child(
        column()
            .gap(px(8.))
            .child(muted("storage/sessions.py", 14.))
            .child(column().gap(px(4.)).child(removed).child(added)),
    )
}

fn image_artifact() -> Div {
    section("18:31").h(px(156.)).child(
        row()
            .gap(px(16.))
            .child(
                row()
                    .w(px(112.))
                    .h(px(72.))
                    .justify_center()
                    .rounded(px(CONTROL_RADIUS))
                    .bg(rgb(FIELD))
                    .child(
                        img("attachment.jpg")
                            .h(px(72.))
                            .w(px(48.))
                            .object_fit(ObjectFit::Cover),
                    ),
            )
            .child(
                column()
                    .gap(px(8.))
                    .child(label("ani-bot-cover.png", 14.))
                    .child(muted("PNG · 1024 × 1536", 14.)),
            ),
    )
}

fn video() -> Div {
    section("18:35").h(px(156.)).child(
        row()
            .gap(px(16.))
            .child(
                row()
                    .w(px(112.))
                    .h(px(72.))
                    .justify_center()
                    .rounded(px(CONTROL_RADIUS))
                    .bg(rgb(FIELD))
                    .child(icon("play", 22., PRIMARY)),
            )
            .child(
                column()
                    .gap(px(8.))
                    .child(label("desktop-tour.mp4", 14.))
                    .child(muted("00:12 · MP4 · 1080p", 14.)),
            ),
    )
}

fn waveform() -> Div {
    let heights = [
        6., 12., 18., 10., 16., 20., 12., 8., 18., 14., 6., 12., 20., 14., 8., 16., 10., 6.,
    ];
    row()
        .w(px(148.))
        .h(px(20.))
        .gap(px(5.))
        .flex_shrink_0()
        .children(heights.map(|height| {
            div()
                .w(px(3.))
                .h(px(height))
                .rounded_full()
                .bg(rgb(PRIMARY))
        }))
}

fn audio() -> Div {
    section("18:42").h(px(124.)).child(
        row().w_full().gap(px(16.)).child(waveform()).child(
            row()
                .flex_1()
                .min_w_0()
                .justify_between()
                .gap(px(12.))
                .child(
                    row()
                        .gap(px(8.))
                        .child(icon("play", 20., PRIMARY))
                        .child(muted("00:18", 14.)),
                )
                .child(label("release-notes.wav", 14.).min_w_0().truncate()),
        ),
    )
}

pub fn artifacts() -> Div {
    column()
        .w_full()
        .gap(px(20.))
        .child(diff())
        .child(image_artifact())
        .child(video())
        .child(audio())
}

pub fn live(data: Option<&crate::inspector::Inspection>, selected: bool) -> Div {
    if !selected {
        return column().child(muted("选择会话查看交付", 14.));
    }
    let Some(data) = data else {
        return column().child(muted("正在读取交付…", 14.));
    };
    column()
        .w_full()
        .gap(px(20.))
        .children(data.deliveries.iter().map(|group| {
            card()
                .p(px(14.))
                .gap(px(12.))
                .child(
                    row()
                        .gap(px(12.))
                        .child(timeline_time(
                            crate::inspector::time_label(&group.timestamp),
                            1.,
                        ))
                        .child(
                            label(
                                group
                                    .title
                                    .as_deref()
                                    .filter(|title| !title.is_empty())
                                    .unwrap_or("会话交付")
                                    .to_owned(),
                                16.,
                            )
                            .min_w_0()
                            .truncate(),
                        ),
                )
                .children(group.items.iter().map(delivery_row))
        }))
}

fn delivery_row(item: &crate::inspector::Delivery) -> Div {
    let action = match item.action.as_str() {
        "commit" => "Git 提交",
        "create" => "新增文件",
        "edit" | "edit_file" => "修改文件",
        "delete" => "删除文件",
        "rename" => "重命名",
        "create_directory" => "创建目录",
        "generate" => "生成媒体",
        _ => "写入文件",
    };
    let detail = match (&item.sha, &item.previous, &item.mime) {
        (Some(sha), _, _) => format!(
            "{} · {}",
            sha.chars().take(8).collect::<String>(),
            item.path
        ),
        (_, Some(previous), _) => format!("{previous} → {}", item.path),
        (_, _, Some(mime)) => format!("{action} · {mime}"),
        _ => action.to_owned(),
    };
    let title = item.title.as_deref().unwrap_or(&item.path);
    let text = column()
        .flex_1()
        .min_w_0()
        .gap(px(4.))
        .child(label(title.to_owned(), 14.).min_w_0().truncate())
        .child(
            muted(
                if item.observed {
                    format!("{detail} · 工具执行期间检测到")
                } else {
                    detail
                },
                12.,
            )
            .min_w_0()
            .truncate(),
        );
    let mut content = row().w_full().gap(px(10.));
    if let Some(path) = item.local_path.as_ref().map(std::path::PathBuf::from) {
        let preview = if item.kind == "image" {
            img(path.clone())
                .w(px(64.))
                .h(px(44.))
                .object_fit(ObjectFit::Contain)
                .into_any_element()
        } else {
            icon("play", 18., PRIMARY).into_any_element()
        };
        content = content.child(
            gpui_kit::base::Button::new(SharedString::from(format!(
                "open-delivery-{}",
                path.display()
            )))
            .accessibility_label(format!("打开 {title}"))
            .w(px(64.))
            .h(px(44.))
            .flex_shrink_0()
            .rounded(px(CONTROL_RADIUS))
            .bg(rgb(FIELD))
            .child(preview)
            .on_click(move |_, _, cx| open_media(path.clone(), cx)),
        );
    } else if item.kind != "file" && item.kind != "commit" && item.action != "delete" {
        content = content.child(muted("文件不可用", 12.));
    } else {
        content = content.child(icon(
            if item.kind == "commit" {
                "package"
            } else {
                "attachment-01"
            },
            16.,
            MUTED,
        ));
    }
    content.child(text)
}

fn open_media(path: std::path::PathBuf, cx: &mut App) {
    let task = cx.background_executor().spawn(async move {
        if path.canonicalize().ok().as_ref() != Some(&path) || !path.is_file() {
            return false;
        }
        std::process::Command::new("/usr/bin/open")
            .arg("--")
            .arg(&path)
            .status()
            .is_ok_and(|status| status.success())
    });
    cx.spawn(async move |cx| {
        if !task.await {
            let _ = cx.update(|_| {
                crate::system_notification::notify("无法打开交付文件", "请确认文件仍然存在。")
            });
        }
    })
    .detach();
}

#[cfg(all(test, feature = "visual-check"))]
mod tests {
    use super::live;
    use crate::inspector::Inspection;
    use gpui_kit::{
        AppContext, Context, IntoElement, Render, TestAppContext, TestDispatcher, Window,
        base::test_support::find, component::Root, init,
    };
    use serde_json::json;

    struct Host;
    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            let data: Inspection = serde_json::from_value(json!({
                "directory": [], "directory_truncated": false, "daily_budget_usd": 1,
                "user_memory": "", "project_memory": "", "binding_history": [],
                "deliveries": [{
                    "title": "更新笔记并生成音频", "timestamp": "2026-11-13T09:00:00Z",
                    "items": [
                        {"path": "notes.txt", "kind": "file", "action": "edit", "observed": true},
                        {"path": "/tmp/project", "kind": "commit", "action": "commit", "title": "Update notes", "sha": "12345678"},
                        {"path": "demo.wav", "kind": "audio", "action": "create", "local_path": "/tmp/demo.wav"},
                        {"path": "missing.wav", "kind": "audio", "action": "create"}
                    ]
                }]
            })).unwrap();
            live(Some(&data), true)
        }
    }

    #[test]
    fn delivery_renders_open_control_only_for_available_media() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| init(cx));
        let (_, window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| Host);
            Root::new(host, window, cx)
        });
        window.run_until_parked();
        window.update(|window, cx| {
            window.draw(cx).clear(cx);
            let media = find(window, &[], &"open-delivery-/tmp/demo.wav".into()).unwrap();
            assert_eq!(media.label(), Some("打开 demo.wav"));
            assert!(find(window, &[], &"open-delivery-/tmp/missing.wav".into()).is_none());
        });
    }
}
