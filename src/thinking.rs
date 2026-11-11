use std::f32::consts::TAU;
use std::time::Instant;

use crate::theme::*;
use gpui_kit::*;

#[derive(IntoElement)]
pub struct Thinking {
    pub id: SharedString,
}

fn shine(time: f32, index: usize) -> f32 {
    let distance = ((time / 1.8).fract() * 1.8 - 0.3 - index as f32 / 7.).abs();
    (1. - distance / 0.3).clamp(0., 1.)
}

impl RenderOnce for Thinking {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let start = window.use_keyed_state(self.id, cx, |_, _| Instant::now());
        let reduced = cx.reduce_motion();
        let time = if reduced {
            0.
        } else {
            start.read(cx).elapsed().as_secs_f32()
        };
        if !reduced {
            window.request_animation_frame();
        }
        row()
            .gap(px(8.))
            .h(px(24.))
            .child(
                icon("think", 18., MUTED).with_transformation(Transformation::rotate(radians(
                    TAU * (time / 3.).fract(),
                ))),
            )
            .child(
                row().children("think...".chars().enumerate().map(|(index, letter)| {
                    let intensity = if reduced { 0. } else { shine(time, index) };
                    let mut color: Hsla = rgb(MUTED).into();
                    color.l *= 1. - intensity * 0.65;
                    label(letter.to_string(), 14.).text_color(color)
                })),
            )
    }
}

#[cfg(test)]
mod tests {
    use super::shine;

    #[test]
    fn shine_moves_across_the_label() {
        assert!(shine(0.3, 0) > shine(0.3, 7));
        assert!(shine(1.3, 7) > shine(1.3, 0));
    }
}
