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
                    .child(label("vespera-cover.png", 14.))
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
