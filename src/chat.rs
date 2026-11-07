use crate::masked_scroll::MaskedScroll;
use crate::{
    fixtures,
    markdown::{code_header, markdown, markdown_surface},
    theme::*,
};
use gpui_kit::base::input::TextareaState;
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

fn composer(draft: &Entity<TextareaState>) -> Div {
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
                    row()
                        .w(px(44.))
                        .h_full()
                        .justify_center()
                        .child(icon("add-01", 20., MUTED)),
                )
                .child(
                    row()
                        .gap(px(12.))
                        .pr(px(5.5))
                        .child(
                            row()
                                .size(px(32.))
                                .justify_center()
                                .child(icon("mic-02", 20., MUTED)),
                        )
                        .child(
                            row()
                                .size(px(33.))
                                .rounded_full()
                                .justify_center()
                                .bg(rgb(PRIMARY))
                                .shadow(vec![BoxShadow {
                                    inset: false,
                                    color: rgba(0xf6671e20).into(),
                                    offset: point(px(0.), px(2.)),
                                    blur_radius: px(5.),
                                    spread_radius: px(0.),
                                }])
                                .child(
                                    div()
                                        .size(px(12.))
                                        .rounded(px(SELECTION_RADIUS))
                                        .bg(rgb(0xffffff)),
                                ),
                        ),
                ),
        )
}

pub fn chat(width: f32, draft: &Entity<TextareaState>) -> Div {
    column()
        .w(px(width))
        .flex_shrink_0()
        .min_w_0()
        .h_full()
        .border_r_1()
        .border_color(rgb(BORDER))
        .pb(px(44.))
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
                    MaskedScroll::new(
                        "conversation-history",
                        140.,
                        column()
                            .gap(px(20.))
                            .pt(px(20.))
                            .px(px(CHAT_INSET))
                            .child(
                                row().w_full().justify_end().child(
                                    column()
                                        .relative()
                                        .w_auto()
                                        .max_w(relative(0.75))
                                        .min_w_0()
                                        .px(px(19.))
                                        .py(px(15.))
                                        .child(
                                            canvas(
                                                |_, _, _| (),
                                                |bounds, (), window, _| {
                                                    let short_side =
                                                        bounds.size.width.min(bounds.size.height);
                                                    window.paint_quad(quad(
                                                        bounds,
                                                        Corners::all(limited_radius(
                                                            16.,
                                                            short_side.into(),
                                                        )),
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
                                        .child(label(fixtures::QUESTION, 14.)),
                                ),
                            )
                            .child(
                                column()
                                    .w_full()
                                    .child(muted("用时 3分钟", 16.).mb(px(8.)))
                                    .child(div().h(px(1.)).w_full().bg(rgb(0xededee)).mb(px(16.)))
                                    .child(
                                        div().text_size(px(14.)).line_height(relative(1.3)).child(
                                            markdown_surface(
                                                markdown("answer", fixtures::ANSWER)
                                                    .code_block_actions(move |block, window, _| {
                                                        code_header(
                                                            block,
                                                            px(width - 2. * CHAT_INSET - 1.),
                                                            window,
                                                        )
                                                    }),
                                            ),
                                        ),
                                    ),
                            ),
                    )
                    .scrollbar()
                    .bottom_overlay(ATTACHMENT_OVERLAY_HEIGHT),
                )
                .child(
                    div()
                        .absolute()
                        .bottom_0()
                        .w_full()
                        .h(px(ATTACHMENT_OVERLAY_HEIGHT))
                        .bg(rgb(BACKGROUND))
                        .child(fade(10.).bottom(px(ATTACHMENT_OVERLAY_HEIGHT))),
                )
                .child(
                    div()
                        .absolute()
                        .left(px(30.))
                        .bottom_0()
                        .child(attachment()),
                ),
        )
        .child(
            div()
                .w_full()
                .px(px(30.))
                .flex_shrink_0()
                .child(composer(draft)),
        )
}
