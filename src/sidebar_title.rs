use crate::theme::*;
use gpui_kit::{base::animation::cubic_bezier, *};
use std::time::Instant;

#[derive(IntoElement)]
pub struct SidebarTitle {
    pub id: SharedString,
    pub text: SharedString,
    pub active: bool,
}

struct Hover {
    started: Option<Instant>,
    width: f32,
}

fn scroll_offset(overflow: f32, elapsed: f32) -> f32 {
    let duration = (overflow / (35. * 2. / 3.)).max(0.45);
    let progress = ((elapsed - 0.35) / duration).clamp(0., 1.);
    overflow * cubic_bezier(0.42, 0., 0.58, 1.)(progress)
}

impl RenderOnce for SidebarTitle {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(
            SharedString::from(format!("sidebar-title:{}:{}", self.id, self.text)),
            cx,
            |_, _| Hover {
                started: None,
                width: 0.,
            },
        );
        let font = if self.active {
            let mut font = emphasis_font();
            font.weight = FontWeight::SEMIBOLD;
            font
        } else {
            body_font()
        };
        let width: f32 = window
            .text_system()
            .shape_line(
                self.text.clone(),
                px(14.),
                &[TextRun {
                    len: self.text.len(),
                    font: font.clone(),
                    color: rgb(INK).into(),
                    background_color: None,
                    underline: None,
                    strikethrough: None,
                }],
                None,
            )
            .width
            .into();
        let hover = state.read(cx);
        let overflow = (width - hover.width).max(0.);
        let scrolling = hover.started.is_some() && overflow > 0.;
        let offset = if cx.reduce_motion() {
            overflow
        } else {
            scroll_offset(
                overflow,
                hover
                    .started
                    .map_or(0., |time| time.elapsed().as_secs_f32()),
            )
        };
        if scrolling && offset < overflow && !cx.reduce_motion() {
            window.request_animation_frame();
        }
        let content = if scrolling {
            label(self.text, 14.)
                .font(font)
                .whitespace_nowrap()
                .absolute()
                .w(px(width))
                .left(px(-offset))
        } else {
            label(self.text, 14.).font(font).w_full().truncate()
        };
        let measure = state.clone();
        div()
            .flex_1()
            .min_w_0()
            .overflow_hidden()
            .child(div().relative().w_full().h(px(14. * 1.3)).child(content))
            .on_children_prepainted(move |bounds, window, cx| {
                let Some(bounds) = bounds.first() else { return };
                let width: f32 = bounds.size.width.into();
                if (measure.read(cx).width - width).abs() > 0.5 {
                    let measure = measure.clone();
                    window.defer(cx, move |_, cx| {
                        measure.update(cx, |state, cx| {
                            state.width = width;
                            cx.notify();
                        });
                    });
                }
            })
            .id(SharedString::from(format!(
                "sidebar-title-hover:{}",
                self.id
            )))
            .on_hover(move |hovered, _, cx| {
                state.update(cx, |state, cx| {
                    state.started = hovered.then(Instant::now);
                    cx.notify();
                });
            })
    }
}

#[cfg(test)]
mod tests {
    use super::scroll_offset;

    #[test]
    fn hover_scroll_delays_then_stops_at_the_end() {
        assert_eq!(scroll_offset(70., 0.2), 0.);
        assert_eq!(scroll_offset(70., 0.35), 0.);
        assert!((scroll_offset(70., 1.85) - 35.).abs() < 0.1);
        assert_eq!(scroll_offset(70., 10.), 70.);
        assert_eq!(scroll_offset(0., 10.), 0.);
    }
}
