use crate::theme::*;
use gpui_kit::base::animation::cubic_bezier;
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use std::time::Instant;

const GROUP_HEIGHT: f32 = 14. * 1.3;
const ITEM_TOP_PADDING: f32 = 10.;

#[derive(IntoElement)]
pub struct ProjectGroup {
    pub id: SharedString,
    pub name: SharedString,
    pub folder: bool,
    pub items: Vec<Div>,
}

struct Expansion {
    expanded: bool,
    from: f32,
    started: Instant,
    height: Option<f32>,
}

impl Expansion {
    fn progress(&self) -> f32 {
        let time = (self.started.elapsed().as_secs_f32() / 0.2).min(1.);
        interpolate(self.from, if self.expanded { 1. } else { 0. }, time)
    }
}

fn interpolate(from: f32, to: f32, time: f32) -> f32 {
    let eased = cubic_bezier(0.42, 0., 0.58, 1.)(time);
    from + (to - from) * eased
}

impl RenderOnce for ProjectGroup {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(self.id.clone(), cx, |_, _| Expansion {
            expanded: true,
            from: 1.,
            started: Instant::now(),
            height: None,
        });
        let expansion = state.read(cx);
        let progress = if cx.reduce_motion() {
            if expansion.expanded { 1. } else { 0. }
        } else {
            expansion.progress()
        };
        let height = expansion.height;
        if !cx.reduce_motion() && expansion.started.elapsed().as_millis() < 200 {
            window.request_animation_frame();
        }
        let toggle = state.clone();
        let measure = state.clone();

        column()
            .w_full()
            .flex_shrink_0()
            .child(
                row()
                    .id(self.id.clone())
                    .debug_selector(move || self.id.to_string())
                    .h(px(GROUP_HEIGHT))
                    .px(px(10.))
                    .gap(px(8.))
                    .rounded(limited_radius(CONTROL_RADIUS, GROUP_HEIGHT))
                    .overflow_hidden()
                    .child(icon(
                        if self.folder {
                            "folder-01"
                        } else {
                            "bubble-chat"
                        },
                        16.5,
                        MUTED,
                    ))
                    .child(
                        label(self.name, 14.)
                            .flex_1()
                            .min_w_0()
                            .text_color(rgb(MUTED))
                            .whitespace_nowrap()
                            .overflow_hidden(),
                    )
                    .on_click(move |_, _, cx| {
                        toggle.update(cx, |state, cx| {
                            state.from = state.progress();
                            state.expanded = !state.expanded;
                            state.started = Instant::now();
                            cx.notify();
                        });
                    }),
            )
            .child(
                div()
                    .w_full()
                    .overflow_hidden()
                    .flex_shrink_0()
                    .min_h_0()
                    .when_some(height, |view, height| {
                        view.h(px((height + ITEM_TOP_PADDING) * progress))
                    })
                    .child(
                        column()
                            .w_full()
                            .flex_shrink_0()
                            .pt(px(ITEM_TOP_PADDING))
                            .opacity(progress)
                            .child(
                                column().w_full().gap(px(10.)).children(
                                    self.items
                                        .into_iter()
                                        .map(|item| div().w_full().pl(px(25.)).child(item)),
                                ),
                            )
                            .on_children_prepainted(move |bounds, window, cx| {
                                let Some(bounds) = bounds.first() else { return };
                                let height: f32 = bounds.size.height.into();
                                if measure.read(cx).height != Some(height) {
                                    let measure = measure.clone();
                                    window.defer(cx, move |_, cx| {
                                        measure.update(cx, |state, cx| {
                                            state.height = Some(height);
                                            cx.notify();
                                        });
                                    });
                                }
                            }),
                    ),
            )
    }
}

#[cfg(test)]
mod tests {
    use super::interpolate;

    #[test]
    fn expansion_is_symmetric_and_can_reverse_from_current_position() {
        assert_eq!(interpolate(0., 1., 0.), 0.);
        assert_eq!(interpolate(0., 1., 1.), 1.);
        assert_eq!(interpolate(0., 1., 0.5), 0.5);
        assert_eq!(interpolate(0.5, 0., 0.), 0.5);
        assert_eq!(interpolate(0.5, 0., 1.), 0.);
    }
}

#[cfg(all(test, feature = "visual-check"))]
mod layout_tests {
    use super::ProjectGroup;
    use crate::theme::column;
    use gpui_kit::{
        AppContext, Bounds, Context, InteractiveElement, IntoElement, Modifiers, ParentElement,
        Pixels, Render, SharedString, Styled, TestAppContext, TestDispatcher, VisualTestContext,
        Window, component::Root, div, init, px,
    };

    struct Host;
    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            column()
                .w(px(220.))
                .gap(px(10.))
                .children(["first", "second"].map(|id| ProjectGroup {
                    id: id.into(),
                    name: id.into(),
                    folder: true,
                    items: vec![div().h(px(14. * 1.3)).child(
                        div().id(SharedString::from(format!("{id}-item")))
                                .debug_selector(move || format!("{id}-item"))
                            .h(px(14. * 1.3)).child("chat"),
                    )],
                }))
        }
    }

    #[test]
    fn collapsed_and_expanded_groups_keep_ten_pixel_gaps() {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| {
            init(cx);
            cx.set_reduce_motion(true);
        });
        let (_, window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| Host);
            Root::new(host, window, cx)
        });
        fn draw(window: &mut VisualTestContext) {
            for _ in 0..4 {
                window.run_until_parked();
                window.update(|window, cx| {
                    window.refresh();
                    window.draw(cx).clear(cx);
                });
            }
        }
        fn bounds(window: &mut VisualTestContext, id: &'static str) -> Bounds<Pixels> {
            window.debug_bounds(id).expect(id)
        }
        draw(window);
        let header = bounds(window, "first");
        let item = bounds(window, "first-item");
        let second = bounds(window, "second");
        assert!(
            (f32::from(header.size.height) - 18.2).abs() <= 0.5,
            "header={header:?}, item={item:?}"
        );
        assert!((f32::from(item.size.height) - 18.2).abs() <= 0.5);
        assert_eq!(item.top() - header.bottom(), px(10.));
        assert_eq!(second.top() - item.bottom(), px(10.));
        window.simulate_click(header.center(), Modifiers::default());
        draw(window);
        assert_eq!(
            bounds(window, "second").top() - bounds(window, "first").bottom(),
            px(10.)
        );
        window.simulate_click(header.center(), Modifiers::default());
        draw(window);
        assert_eq!(
            bounds(window, "second").top() - bounds(window, "first-item").bottom(),
            px(10.)
        );
    }
}
