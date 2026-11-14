use crate::theme::*;
use gpui_kit::{
    base::{Button, animation::ease_in_out_cubic},
    prelude::FluentBuilder,
    *,
};
use std::time::Instant;

#[derive(IntoElement)]
pub(crate) struct CollapsibleSection {
    id: &'static str,
    title: &'static str,
    body: Div,
}

impl CollapsibleSection {
    pub(crate) fn new(id: &'static str, title: &'static str, body: Div) -> Self {
        Self { id, title, body }
    }
}

struct Expansion {
    expanded: bool,
    motion: Option<(f32, Instant)>,
    height: Option<Pixels>,
}

impl Expansion {
    fn progress(&self, now: Instant) -> f32 {
        let target = if self.expanded { 1. } else { 0. };
        self.motion.map_or(target, |(from, started)| {
            let time = (now.duration_since(started).as_secs_f32() / 0.2).min(1.);
            from + (target - from) * ease_in_out_cubic(time)
        })
    }
}

impl RenderOnce for CollapsibleSection {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        const GAP: Pixels = INSPECTOR_SECTION_GAP;
        let state = window.use_keyed_state(self.id, cx, |_, _| Expansion {
            expanded: true,
            motion: None,
            height: None,
        });
        let expansion = state.read(cx);
        let expanded = expansion.expanded;
        let height = expansion.height;
        let now = Instant::now();
        let progress = if cx.reduce_motion() {
            if expanded { 1. } else { 0. }
        } else {
            if expansion
                .motion
                .is_some_and(|(_, started)| now.duration_since(started).as_secs_f32() < 0.2)
            {
                window.request_animation_frame();
            }
            expansion.progress(now)
        };
        let toggle = state.clone();

        card()
            .p(INSPECTOR_SECTION_PADDING)
            .child(
                Button::new(self.id)
                    .debug_selector(|| self.id.into())
                    .accessibility_label(if expanded {
                        format!("收起{}", self.title)
                    } else {
                        format!("展开{}", self.title)
                    })
                    .w_full()
                    .justify_start()
                    .gap(px(8.))
                    .child(section_marker())
                    .child(label(self.title, 16.))
                    .on_click(move |_, _, cx| {
                        let reduce_motion = cx.reduce_motion();
                        toggle.update(cx, |expansion, cx| {
                            let now = Instant::now();
                            expansion.motion =
                                (!reduce_motion).then(|| (expansion.progress(now), now));
                            expansion.expanded = !expansion.expanded;
                            cx.notify();
                        });
                    }),
            )
            .children((expanded || progress > 0.).then(|| {
                div()
                    .w_full()
                    .flex_shrink_0()
                    .overflow_hidden()
                    .when_some(height, |clip, height| clip.h((height + GAP) * progress))
                    .child(
                        column()
                            .w_full()
                            .flex_shrink_0()
                            .pt(GAP)
                            .opacity(progress)
                            .child(self.body)
                            .on_children_prepainted(move |bounds, window, cx| {
                                let Some(bounds) = bounds.first() else { return };
                                let height = bounds.size.height;
                                if state.read(cx).height != Some(height) {
                                    let state = state.clone();
                                    window.defer(cx, move |_, cx| {
                                        state.update(cx, |expansion, cx| {
                                            expansion.height = Some(height);
                                            cx.notify();
                                        });
                                    });
                                }
                            }),
                    )
            }))
    }
}

#[cfg(all(test, feature = "visual-check"))]
mod layout_tests {
    use super::CollapsibleSection;
    use crate::theme::column;
    use gpui_kit::{
        AppContext, Context, InteractiveElement, IntoElement, Modifiers, ParentElement, Render,
        Styled, TestAppContext, TestDispatcher, VisualTestContext, Window, component, div, init,
        point, px,
    };

    struct Host(bool);

    impl Render for Host {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            let section = if self.0 {
                let mut tasks = crate::background_tasks::BackgroundTasks::default();
                tasks.update(serde_json::from_value(serde_json::json!({
                    "id": "task", "role": "executor", "prompt": "Run checks", "status": "running"
                })).unwrap());
                tasks.section().unwrap()
            } else {
                CollapsibleSection::new("timeline-toggle", "时间线", div().h(px(80.)))
            };
            column().w(px(300.)).child(section).child(
                div()
                    .id("next-section")
                    .debug_selector(|| "next-section".into())
                    .h(px(20.)),
            )
        }
    }

    #[test]
    fn clicking_right_edge_of_timeline_header_collapses_and_restores_body() {
        assert_eq!(collapse_and_restore(false, "timeline-toggle"), px(92.));
    }

    #[test]
    fn clicking_right_edge_of_background_tasks_header_collapses_and_restores_body() {
        assert!(collapse_and_restore(true, "background-tasks-toggle") > px(12.));
    }

    fn collapse_and_restore(tasks: bool, selector: &'static str) -> gpui_kit::Pixels {
        let mut cx = TestAppContext::build(TestDispatcher::new(0), None);
        cx.update(|cx| {
            init(cx);
            cx.set_reduce_motion(true);
        });
        let (_, mut window) = cx.add_window_view(|window, cx| {
            let host = cx.new(|_| Host(tasks));
            component::Root::new(host, window, cx)
        });
        let draw = |window: &mut VisualTestContext| {
            window.run_until_parked();
            window.update(|window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            });
            let header = window.debug_bounds(selector).unwrap();
            let next = window.debug_bounds("next-section").unwrap();
            (
                point(header.right() - px(4.), header.center().y),
                next.top(),
            )
        };
        let (trigger, expanded_top) = draw(&mut window);
        window.simulate_click(trigger, Modifiers::default());
        let (_, collapsed_top) = draw(&mut window);
        window.simulate_click(trigger, Modifiers::default());
        assert_eq!(draw(&mut window).1, expanded_top);
        expanded_top - collapsed_top
    }
}
