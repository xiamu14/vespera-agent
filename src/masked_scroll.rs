use crate::theme::{column, fade_gradient};

use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;

#[derive(IntoElement)]
pub struct MaskedScroll {
    id: SharedString,
    mask_height: f32,
    bottom_overlay: f32,
    scrollbar: bool,
    content: Div,
    follow_bottom: bool,
    external_scroll: Option<ScrollHandle>,
}

impl MaskedScroll {
    pub fn new(id: impl Into<SharedString>, mask_height: f32, content: Div) -> Self {
        Self {
            id: id.into(),
            mask_height,
            bottom_overlay: 0.,
            scrollbar: false,
            content,
            follow_bottom: false,
            external_scroll: None,
        }
    }

    pub fn bottom_overlay(mut self, height: f32) -> Self {
        self.bottom_overlay = height;
        self
    }

    pub fn scroll_handle(mut self, handle: ScrollHandle) -> Self {
        self.external_scroll = Some(handle);
        self
    }

    pub fn follow_bottom(mut self, follow: bool) -> Self {
        self.follow_bottom = follow;
        self
    }

    pub fn scrollbar(mut self) -> Self {
        self.scrollbar = true;
        self
    }
}

fn fade_opacity(remaining: Pixels) -> f32 {
    (f32::from(remaining) / 24.).clamp(0., 1.)
}

impl RenderOnce for MaskedScroll {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state =
            window.use_keyed_state(self.id.clone(), cx, |_, _| (ScrollHandle::new(), false));
        let (internal, overflow) = state.read(cx).clone();
        let scroll = self.external_scroll.unwrap_or(internal);
        let overlay = self.bottom_overlay;
        if self.follow_bottom && scroll.max_offset().y + scroll.offset().y < px(40.) {
            scroll.scroll_to_bottom();
        }
        let scrollbar =
            (self.scrollbar && overflow).then(|| crate::chat_scrollbar::chat_scrollbar(&scroll));

        div()
            .relative()
            .w_full()
            .flex_1()
            .min_h_0()
            .overflow_hidden()
            .child(
                column()
                    .id(self.id)
                    .size_full()
                    .overflow_y_scroll()
                    .restrict_scroll_to_axis()
                    .track_scroll(&scroll)
                    .child(self.content.flex_shrink_0())
                    .when(overflow, |view| {
                        view.child(div().flex_shrink_0().h(px(overlay + 10.)))
                    }),
            )
            .child(
                canvas(
                    // Read the offset after the scroll viewport has laid out this frame.
                    move |_, window, cx| {
                        let viewport = scroll.bounds().size.height;
                        // Measure the content alone so the spacer cannot keep itself enabled.
                        let next = scroll
                            .bounds_for_item(0)
                            .is_some_and(|content| content.size.height > viewport - px(overlay));
                        if next != overflow {
                            window.defer(cx, move |_, cx| {
                                state.update(cx, |state, cx| {
                                    state.1 = next;
                                    cx.notify();
                                });
                            });
                        }
                        if next {
                            fade_opacity(scroll.max_offset().y + scroll.offset().y)
                        } else {
                            0.
                        }
                    },
                    |bounds, opacity, window, _| {
                        if opacity > 0. {
                            window.paint_quad(fill(bounds, fade_gradient(opacity)));
                        }
                    },
                )
                .absolute()
                .bottom_0()
                .left_0()
                .w_full()
                .h(px(self.mask_height)),
            )
            .children(scrollbar)
    }
}

#[cfg(test)]
mod tests {
    use super::{fade_opacity, px};

    #[test]
    fn fade_disappears_within_the_last_24_pixels() {
        assert_eq!(fade_opacity(px(30.)), 1.);
        assert_eq!(fade_opacity(px(12.)), 0.5);
        assert_eq!(fade_opacity(px(0.)), 0.);
        assert_eq!(fade_opacity(px(-1.)), 0.);
    }

    #[cfg(feature = "visual-check")]
    #[test]
    fn fast_wheel_updates_fade_in_the_same_frame() {
        use super::*;

        struct ScrollFixture;

        impl Render for ScrollFixture {
            fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
                column().size_full().child(MaskedScroll::new(
                    "test-scroll",
                    64.,
                    div().h(px(1600.)).w_full(),
                ))
            }
        }

        let mut app = TestApp::new();
        let mut view = app.open_window(|_, _| ScrollFixture);
        view.draw();
        view.draw();
        let handle: AnyWindowHandle = view.handle().into();

        let mut frame_has_fade = |lines| {
            app.update(|cx| {
                handle
                    .update(cx, |_, window, cx| {
                        for _ in 0..3 {
                            window.dispatch_event(
                                PlatformInput::Scroll(ScrollEvent {
                                    position: point(px(20.), px(20.)),
                                    delta: ScrollDelta::Lines(point(0., lines)),
                                    ..Default::default()
                                }),
                                cx,
                            );
                        }
                        window.draw(cx).clear(cx);
                        window
                            .painted_quads()
                            .iter()
                            .any(|quad| quad.background.as_solid().is_none())
                    })
                    .unwrap()
            })
        };

        assert!(frame_has_fade(0.));
        assert!(!frame_has_fade(-100.), "Fade must disappear on arrival");
        assert!(frame_has_fade(100.), "Fade must return on departure");
    }
}
