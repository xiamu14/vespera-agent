use crate::theme::{column, fade};

use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;

#[derive(IntoElement)]
pub struct MaskedScroll {
    id: &'static str,
    mask_height: f32,
    bottom_overlay: f32,
    scrollbar: bool,
    content: Div,
}

impl MaskedScroll {
    pub fn new(id: &'static str, mask_height: f32, content: Div) -> Self {
        Self {
            id,
            mask_height,
            bottom_overlay: 0.,
            scrollbar: false,
            content,
        }
    }

    pub fn bottom_overlay(mut self, height: f32) -> Self {
        self.bottom_overlay = height;
        self
    }

    pub fn scrollbar(mut self) -> Self {
        self.scrollbar = true;
        self
    }
}

fn bottom_treatment(content: f32, viewport: f32, overlay: f32, remaining: f32) -> (bool, f32) {
    let overflow = content > viewport - overlay;
    (
        overflow,
        if overflow {
            (remaining / 24.).clamp(0., 1.)
        } else {
            0.
        },
    )
}

impl RenderOnce for MaskedScroll {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(self.id, cx, |_, _| (ScrollHandle::new(), (false, 0.)));
        let (scroll, treatment) = state.read(cx).clone();
        let (overflow, opacity) = treatment;
        let overlay = self.bottom_overlay;

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
            .when(opacity > 0., |view| {
                view.child(fade(self.mask_height).opacity(opacity))
            })
            .when(self.scrollbar && overflow, |view| {
                view.child(crate::chat_scrollbar::chat_scrollbar(&scroll))
            })
            .on_children_prepainted(move |_, window, cx| {
                // Measure the content alone so the spacer cannot keep itself enabled.
                let Some(content) = scroll.bounds_for_item(0) else {
                    return;
                };
                let next = bottom_treatment(
                    content.size.height.into(),
                    scroll.bounds().size.height.into(),
                    overlay,
                    (scroll.max_offset().y + scroll.offset().y).into(),
                );
                if next != treatment {
                    let state = state.clone();
                    window.defer(cx, move |_, cx| {
                        state.update(cx, |state, cx| {
                            state.1 = next;
                            cx.notify();
                        });
                    });
                }
            })
    }
}

#[cfg(test)]
mod tests {
    use super::bottom_treatment;

    #[test]
    fn fits_without_padding_and_fades_at_the_end() {
        assert_eq!(bottom_treatment(300., 300., 0., 0.), (false, 0.));
        assert_eq!(bottom_treatment(220., 300., 93., 30.), (true, 1.));
        assert_eq!(bottom_treatment(400., 300., 0., 12.), (true, 0.5));
        assert_eq!(bottom_treatment(400., 300., 0., 0.), (true, 0.));
    }
}
