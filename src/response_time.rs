use crate::theme::*;
use gpui_kit::*;

#[derive(IntoElement)]
pub struct ResponseTime {
    pub seconds: u64,
    pub running: bool,
}

impl RenderOnce for ResponseTime {
    fn render(self, window: &mut Window, _: &mut App) -> impl IntoElement {
        if self.running {
            window.request_animation_frame();
        }
        let seconds = self.seconds.max(1);
        let elapsed = if seconds >= 60 {
            format!("用时 {}分{}秒", seconds / 60, seconds % 60)
        } else {
            format!("用时 {}秒", seconds)
        };
        column()
            .gap(px(8.))
            .child(muted(elapsed, 16.))
            .child(div().h(px(1.)).w_full().bg(rgb(0xededee)))
    }
}
