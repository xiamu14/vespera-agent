use gpui_kit::base::{Scrollbar, ScrollbarHandle, ScrollbarMode};
use gpui_kit::*;

const TRACK_HEIGHT: f32 = 125.;

// Map the full chat viewport onto the short design track in both directions.
#[derive(Clone)]
pub struct ChatScrollHandle(pub ScrollHandle);

impl ChatScrollHandle {
    fn scale(&self) -> f32 {
        TRACK_HEIGHT / f32::from(self.0.bounds().size.height).max(1.)
    }
}

impl ScrollbarHandle for ChatScrollHandle {
    fn viewport_bounds(&self) -> Bounds<Pixels> {
        self.0.bounds()
    }

    fn offset(&self) -> Point<Pixels> {
        let mut offset = self.0.offset();
        offset.y *= self.scale();
        offset
    }

    fn set_offset(&self, offset: Point<Pixels>) {
        let y = (offset.y / self.scale()).clamp(-self.0.max_offset().y, px(0.));
        self.0.set_offset(point(self.0.offset().x, y));
    }

    fn content_size(&self) -> Size<Pixels> {
        size(
            px(8.),
            (self.0.bounds().size.height + self.0.max_offset().y) * self.scale(),
        )
    }
}

pub fn chat_scrollbar(scroll: &ScrollHandle) -> Div {
    div()
        .absolute()
        .right(px(5.))
        .top(relative(0.5))
        .mt(px(-62.5))
        .w(px(8.))
        .h(px(TRACK_HEIGHT))
        .rounded_full()
        .overflow_hidden()
        .bg(rgb(0xefefef))
        .child(
            Scrollbar::vertical(&ChatScrollHandle(scroll.clone()))
                .mode(ScrollbarMode::Always)
                .viewport_from_layout()
                .styles(|style| {
                    style
                        .track(|track| track.width(px(8.)).bg(hsla(0., 0., 0., 0.)))
                        .thumb(|thumb| {
                            thumb
                                .width(px(4.))
                                .inset(px(2.))
                                .radius(px(2.))
                                .min_length(px(20.))
                                .bg(rgb(0xfa945d))
                        })
                        .thumb_hover(|thumb| thumb.width(px(4.)).bg(rgb(0xfa945d)))
                        .thumb_active(|thumb| thumb.width(px(4.)).bg(rgb(0xfa945d)))
                }),
        )
}
