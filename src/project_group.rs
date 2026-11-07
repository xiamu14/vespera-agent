use crate::theme::*;
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use std::time::Instant;

const GROUP_HEIGHT: f32 = 33.;
const ITEM_TOP_PADDING: f32 = 4.;
const GUIDE_COLOR: u32 = 0xe8e8ea;

#[derive(IntoElement)]
pub struct ProjectGroup {
    pub name: &'static str,
    pub folder: bool,
    pub has_active_chat: bool,
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
    let eased = time * time * (3. - 2. * time);
    from + (to - from) * eased
}

impl RenderOnce for ProjectGroup {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(self.name, cx, |_, _| Expansion {
            expanded: true,
            from: 1.,
            started: Instant::now(),
            height: None,
        });
        let expansion = state.read(cx);
        let progress = expansion.progress();
        let height = expansion.height;
        let expanded = expansion.expanded;
        if expansion.started.elapsed().as_millis() < 200 {
            window.request_animation_frame();
        }
        let toggle = state.clone();
        let measure = state.clone();
        let item_count = self.items.len();

        column()
            .w_full()
            .child(
                row()
                    .id(self.name)
                    .h(px(GROUP_HEIGHT))
                    .px(px(10.))
                    .gap(px(8.))
                    .rounded(limited_radius(CONTROL_RADIUS, GROUP_HEIGHT))
                    .when(expanded, |header| header.bg(rgb(FIELD)))
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
                        label(self.name, 18.)
                            .flex_1()
                            .min_w_0()
                            .when(self.has_active_chat, |title| title.font(emphasis_font()))
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
                    .when_some(height, |view, height| {
                        view.h(px((height + ITEM_TOP_PADDING) * progress))
                    })
                    .child(
                        column()
                            .w_full()
                            .pt(px(ITEM_TOP_PADDING))
                            .opacity(progress)
                            .child(column().w_full().children(
                                self.items.into_iter().enumerate().map(|(index, item)| {
                                    session_branch(item, index + 1 == item_count)
                                }),
                            ))
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

fn session_branch(item: Div, last: bool) -> Div {
    div()
        .relative()
        .w_full()
        .pl(px(25.))
        .child(
            div()
                .absolute()
                .top_0()
                .left(px(17.))
                .w(px(8.))
                .h(relative(0.5))
                .rounded_bl(px(DETAIL_RADIUS))
                .border_l_1()
                .border_b_1()
                .border_color(rgb(GUIDE_COLOR)),
        )
        .when(!last, |branch| {
            branch.child(
                div()
                    .absolute()
                    .top_0()
                    .bottom_0()
                    .left(px(17.))
                    .w(px(1.))
                    .bg(rgb(GUIDE_COLOR)),
            )
        })
        .child(item)
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
