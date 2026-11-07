use gpui_kit::*;

pub const BACKGROUND: u32 = 0xfdfdfd;
pub const INK: u32 = 0x555555;
pub const MUTED: u32 = 0x66696d;
pub const PRIMARY: u32 = 0xf6671e;
pub const PRIMARY_WASH: u32 = 0xfff2eb;
pub const BORDER: u32 = 0xf5f5f6;
pub const FIELD: u32 = 0xf6f6f6;
pub const PANEL_TOP: f32 = 20.;
pub const INSPECTOR_TAB_HEIGHT: f32 = 52.;
pub const PANEL_RADIUS: f32 = 18.;
pub const CONTROL_RADIUS: f32 = 10.;
pub const DETAIL_RADIUS: f32 = 6.;
pub const SELECTION_RADIUS: f32 = 4.;

pub fn limited_radius(radius: f32, short_side: f32) -> Pixels {
    px(radius.min(short_side * 0.30))
}

pub fn body_font() -> Font {
    let mut font = font("Arial");
    font.fallbacks = Some(FontFallbacks::from_fonts(vec!["vivo Sans".into()]));
    font
}

pub fn emphasis_font() -> Font {
    let mut font = font("Arial");
    font.weight = FontWeight::BOLD;
    // Core Text matches this family alias to vivoSans-Medium even for Arial-BoldMT.
    font.fallbacks = Some(FontFallbacks::from_fonts(vec!["vivo Sans Medium".into()]));
    font
}

pub fn code_font() -> Font {
    let mut font = font("Operator Mono");
    // GPUI maps OperatorMono-Medium's Core Text weight to 350.
    font.weight = FontWeight(350.);
    font
}

pub fn column_widths(width: f32, expanded: bool) -> (f32, f32) {
    let sidebar = if expanded {
        260.
    } else {
        (width * 0.16035746).clamp(230., 260.)
    };
    let remaining = width - sidebar;
    let chat = remaining * 0.6;
    (sidebar, chat)
}

pub fn row() -> Div {
    div().flex().flex_row().items_center()
}

pub fn column() -> Div {
    div().flex().flex_col()
}

pub fn label(value: impl Into<SharedString>, size: f32) -> Div {
    div()
        .text_size(px(size))
        .line_height(relative(1.3))
        .child(value.into())
}

pub fn muted(value: impl Into<SharedString>, size: f32) -> Div {
    label(value, size).text_color(rgb(MUTED))
}

pub fn timeline_time(time: &'static str, alpha: f32) -> Div {
    row()
        .flex_shrink_0()
        .gap(px(12.))
        .child(
            div()
                .size(px(6.))
                .relative()
                .left(px(6.))
                .flex_shrink_0()
                .rounded_full()
                .bg(rgb(PRIMARY).opacity(alpha)),
        )
        .child(muted(time, 14.).flex_shrink_0())
}

pub fn icon(name: &str, size: f32, color: u32) -> Svg {
    svg()
        .path(format!("icons/{name}.svg"))
        .size(px(size))
        .text_color(rgb(color))
        .flex_shrink_0()
}

pub fn card() -> Div {
    column()
        .relative()
        .flex_shrink_0()
        .w_full()
        .rounded(px(PANEL_RADIUS))
        .bg(rgb(BACKGROUND))
        .border_1()
        .border_color(rgb(BORDER))
        .shadow(vec![BoxShadow {
            inset: false,
            color: rgba(0x00000002).into(),
            offset: point(px(0.), px(1.)),
            blur_radius: px(6.),
            spread_radius: px(0.),
        }])
}

pub fn inspector_section(header: Div) -> Div {
    card().p(px(14.)).gap(px(12.)).child(
        row()
            .w_full()
            .gap(px(8.))
            .child(
                div()
                    .w(px(2.5))
                    .h(px(16.))
                    .flex_shrink_0()
                    .rounded_full()
                    .bg(rgb(PRIMARY)),
            )
            .child(header),
    )
}

pub fn composer_surface() -> Div {
    column()
        .rounded(px(PANEL_RADIUS))
        .bg(rgb(0xffffff))
        .border_1()
        .border_color(rgb(0xf1f1f1))
        .shadow(vec![
            BoxShadow {
                inset: false,
                color: rgba(0x00000012).into(),
                offset: point(px(0.), px(2.)),
                blur_radius: px(3.),
                spread_radius: px(0.),
            },
            BoxShadow {
                inset: false,
                color: rgba(0x00000008).into(),
                offset: point(px(0.), px(1.)),
                blur_radius: px(7.),
                spread_radius: px(0.),
            },
        ])
}

pub fn fade(height: f32) -> Div {
    // shortcut: opacity fade, replace with a renderer backdrop pass for live progressive blur.
    div()
        .absolute()
        .bottom_0()
        .left_0()
        .w_full()
        .h(px(height))
        .bg(linear_gradient(
            180.,
            linear_color_stop(hsla(0., 0., 0.992, 0.), 0.),
            linear_color_stop(hsla(0., 0., 0.992, 1.), 1.),
        ))
}

pub fn usage_ring() -> Div {
    div()
        .relative()
        .size(px(20.))
        .child(icon("usage-track", 20., PRIMARY_WASH))
        .child(
            div()
                .absolute()
                .inset_0()
                .child(icon("progress", 20., PRIMARY)),
        )
}

#[cfg(test)]
mod tests {
    use super::{column_widths, limited_radius};
    use gpui_kit::px;

    #[test]
    fn small_surfaces_limit_their_corner_radius() {
        assert_eq!(limited_radius(16., 48.), px(48. * 0.30));
        assert_eq!(limited_radius(16., 72.), px(16.));
        assert_eq!(limited_radius(6., 26.), px(6.));
    }

    #[test]
    fn columns_always_split_remaining_width_at_three_to_two() {
        assert_eq!(column_widths(1280., false), (230., 630.));
        for width in [1140., 1280., 1440., 1920.] {
            for expanded in [false, true] {
                let (sidebar, chat) = column_widths(width, expanded);
                if expanded {
                    assert_eq!(sidebar, 260.);
                }
                assert!((chat / (width - sidebar - chat) - 1.5).abs() < 0.001);
            }
        }
    }
}
