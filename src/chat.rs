use crate::{Vespera, live_chat::filename, masked_scroll::MaskedScroll};
use crate::{
    fixtures,
    markdown::{code_header, markdown, markdown_surface},
    theme::*,
};
use gpui_kit::base::{Button, input::TextareaState};
use gpui_kit::*;

const THUMBNAIL_WIDTH: f32 = 54.;
const THUMBNAIL_HEIGHT: f32 = 59.;
const ATTACHMENT_GAP: f32 = 4.;
const CAPTION_HEIGHT: f32 = 16.;
const REMOVE_SIZE: f32 = 15.;
const CHAT_INSET: f32 = 30.;
const ATTACHMENT_OVERLAY_HEIGHT: f32 = THUMBNAIL_HEIGHT + ATTACHMENT_GAP + CAPTION_HEIGHT;

fn attachment() -> Div {
    column()
        .w(px(THUMBNAIL_WIDTH + REMOVE_SIZE * 0.5))
        .gap(px(ATTACHMENT_GAP))
        .ml(px(8.))
        .relative()
        .child(
            img("attachment-thumbnail.png")
                .w(px(THUMBNAIL_WIDTH))
                .h(px(THUMBNAIL_HEIGHT))
                .object_fit(ObjectFit::Cover)
                .rounded(px(CONTROL_RADIUS)),
        )
        .child(
            label("界面截图", 12.)
                .line_height(px(CAPTION_HEIGHT))
                .text_color(rgb(MUTED)),
        )
        .child(
            row()
                .absolute()
                .left(px(THUMBNAIL_WIDTH - REMOVE_SIZE * 0.5))
                .top_0()
                .size(px(REMOVE_SIZE))
                .justify_center()
                .rounded_full()
                .bg(rgb(0xff626c))
                .child(icon("cancel-01", 9., 0xffffff)),
        )
}

fn composer(draft: &Entity<TextareaState>, app: &Vespera, cx: &Context<Vespera>) -> Div {
    let has_text = !draft.read(cx).value().trim().is_empty();
    let turn = app.selected_turn();
    let running = turn.is_some_and(|turn| turn.running);
    let answering = running && turn.is_some_and(|turn| turn.question.is_some());
    let stopping = turn.is_some_and(|turn| turn.stopping || turn.answering);
    let ready = app
        .chats
        .as_ref()
        .is_some_and(|chats| chats.history.visible().is_some());
    let can_send = if answering {
        false
    } else {
        has_text || !app.selected_files().is_empty()
    };
    let accent = if app.chats.is_none() {
        has_text
    } else {
        ready && (running || can_send)
    };
    let stop = running;
    let input = draft.clone();

    composer_surface()
        .h(px(108.))
        .w_full()
        .flex_shrink_0()
        .relative()
        .p(px(14.))
        .child(crate::text_fields::draft_field(draft))
        .child(
            row()
                .absolute()
                .left(px(8.))
                .right(px(8.))
                .bottom(px(8.))
                .h(px(36.))
                .justify_between()
                .child(
                    Button::new("composer-attachment")
                        .accessibility_label("添加附件")
                        .disabled(!ready || running)
                        .w(px(44.))
                        .h_full()
                        .child(icon("add-01", 20., MUTED))
                        .on_click(cx.listener(|this, _, _, cx| this.choose_attachments(cx))),
                )
                .child(
                    row()
                        .gap(px(12.))
                        .pr(px(5.5))
                        .child(
                            Button::new("composer-microphone")
                                .accessibility_label("语音输入不可用")
                                .disabled(true)
                                .size(px(32.))
                                .child(icon("mic-off", 20., MUTED)),
                        )
                        .child(
                            Button::new("composer-send")
                                .accessibility_label(if stop { "停止" } else { "发送消息" })
                                .disabled(!ready || answering || stopping || (!stop && !can_send))
                                .size(px(33.))
                                .rounded_full()
                                .bg(rgb(if accent { PRIMARY } else { FIELD }))
                                .shadow(if accent {
                                    vec![BoxShadow {
                                        inset: false,
                                        color: rgba(0xf6671e20).into(),
                                        offset: point(px(0.), px(2.)),
                                        blur_radius: px(5.),
                                        spread_radius: px(0.),
                                    }]
                                } else {
                                    vec![]
                                })
                                .child(if stop {
                                    div()
                                        .size(px(12.))
                                        .rounded(px(SELECTION_RADIUS))
                                        .bg(rgb(0xffffff))
                                        .into_any_element()
                                } else {
                                    icon("arrow-up-02", 20., if accent { 0xffffff } else { MUTED })
                                        .into_any_element()
                                })
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    if stop {
                                        this.stop_turn(cx);
                                    } else {
                                        this.submit_draft(&input, window, cx);
                                    }
                                })),
                        ),
                ),
        )
}

pub(crate) fn user_message(
    id: impl Into<SharedString>,
    text: impl Into<SharedString>,
    width: f32,
) -> Div {
    row().w_full().justify_end().child(
        column()
            .relative()
            .w_auto()
            .max_w(relative(0.75))
            .min_w_0()
            .px(px(15.))
            .py(px(11.))
            .child(
                canvas(
                    |_, _, _| (),
                    |bounds, (), window, _| {
                        let short_side = bounds.size.width.min(bounds.size.height);
                        window.paint_quad(quad(
                            bounds,
                            Corners::all(limited_radius(16., short_side.into())),
                            rgb(0xffffff),
                            Edges::all(px(1.)),
                            rgb(0xdddddf),
                            BorderStyle::default(),
                        ));
                    },
                )
                .absolute()
                .inset_0(),
            )
            .child(crate::message_references::MessageReferences {
                id: id.into(),
                text: text.into(),
                max_width: (width - 60.) * 0.75 - 30.,
            }),
    )
}

pub(crate) fn assistant_message(
    id: impl Into<ElementId>,
    text: impl Into<SharedString>,
    width: f32,
) -> Div {
    div()
        .w_full()
        .text_size(px(14.))
        .line_height(relative(1.3))
        .child(markdown_surface(markdown(id, text).code_block_actions(
            move |block, window, _| code_header(block, px(width - 2. * CHAT_INSET - 1.), window),
        )))
}

pub fn chat(
    width: f32,
    draft: &Entity<TextareaState>,
    app: &Vespera,
    cx: &Context<Vespera>,
) -> Div {
    let chats = app.chats.as_ref();
    let preview = chats.is_none();
    let files = app.selected_files();
    let history = chats.map_or_else(
        || {
            column()
                .gap(px(20.))
                .pt(px(20.))
                .px(px(CHAT_INSET))
                .child(user_message(
                    "preview-user-message",
                    fixtures::QUESTION,
                    width,
                ))
                .child(
                    column()
                        .w_full()
                        .child(muted("用时 3分钟", 16.).mb(px(8.)))
                        .child(div().h(px(1.)).w_full().bg(rgb(0xededee)).mb(px(16.)))
                        .child(assistant_message("answer", fixtures::ANSWER, width)),
                )
        },
        |chats| crate::chat_history::live_history(chats, app.selected_turn(), width, cx),
    );
    let scroll_id = chats
        .and_then(|chats| chats.selected.as_deref())
        .map(|id| format!("conversation-history-{id}"))
        .unwrap_or_else(|| "conversation-history".into());
    let overlay = if preview || !files.is_empty() {
        ATTACHMENT_OVERLAY_HEIGHT
    } else {
        0.
    };
    column()
        .w(px(width))
        .flex_shrink_0()
        .min_w_0()
        .h_full()
        .border_r_1()
        .border_color(rgb(BORDER))
        .pb(px(PANEL_TOP))
        .relative()
        .child(
            column()
                .relative()
                .w_full()
                .flex_1()
                .min_h_0()
                .mb(px(6.))
                .overflow_hidden()
                .child(
                    MaskedScroll::new(scroll_id, 140., history)
                        .scroll_handle(app.timeline.scroll.clone())
                        .follow_bottom(app.selected_turn().is_some_and(|turn| turn.running))
                        .scrollbar()
                        .bottom_overlay(overlay),
                )
                .children((overlay > 0.).then(|| {
                    div()
                        .absolute()
                        .bottom_0()
                        .w_full()
                        .h(px(ATTACHMENT_OVERLAY_HEIGHT))
                        .bg(rgb(BACKGROUND))
                        .child(fade(10.).bottom(px(ATTACHMENT_OVERLAY_HEIGHT)))
                }))
                .children(preview.then(|| {
                    div()
                        .absolute()
                        .left(px(30.))
                        .bottom_0()
                        .child(attachment())
                }))
                .children((!files.is_empty()).then(|| {
                    row()
                        .absolute()
                        .left(px(CHAT_INSET))
                        .bottom_0()
                        .gap(px(10.))
                        .children(files.iter().map(|path| attachment_preview(path, cx)))
                })),
        )
        .child(
            div()
                .w_full()
                .px(px(30.))
                .flex_shrink_0()
                .child(composer(draft, app, cx)),
        )
}

fn attachment_preview(path: &str, cx: &Context<Vespera>) -> Div {
    let name = filename(path).to_owned();
    let path = path.to_owned();
    let remove = path.clone();
    let image = std::path::Path::new(&path)
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| {
            ["png", "jpg", "jpeg", "gif", "webp"].contains(&ext.to_ascii_lowercase().as_str())
        });
    let thumbnail = if image {
        img(std::path::PathBuf::from(&path))
            .size(px(54.))
            .object_fit(ObjectFit::Cover)
            .into_any_element()
    } else {
        icon("attachment-01", 22., MUTED).into_any_element()
    };
    column()
        .w(px(68.))
        .gap(px(4.))
        .child(
            row()
                .relative()
                .w(px(54.))
                .h(px(54.))
                .justify_center()
                .rounded(px(CONTROL_RADIUS))
                .bg(rgb(FIELD))
                .child(thumbnail)
                .child(
                    Button::new(SharedString::from(format!("remove-attachment-{path}")))
                        .accessibility_label("移除附件")
                        .absolute()
                        .right_0()
                        .top_0()
                        .size(px(16.))
                        .rounded_full()
                        .bg(rgb(0xff626c))
                        .child(icon("cancel-01", 9., 0xffffff))
                        .on_click(
                            cx.listener(move |this, _, _, cx| this.remove_attachment(&remove, cx)),
                        ),
                ),
        )
        .child(label(name, 12.).truncate())
}
