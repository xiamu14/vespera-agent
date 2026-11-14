use gpui_kit::*;

pub const BACKGROUND: u32 = 0xfdfdfd;
pub const INK: u32 = 0x555555;
pub const MUTED: u32 = 0x66696d;
pub const PRIMARY: u32 = 0xf6671e;
pub const PROGRESS_TRACK: u32 = 0xffd6bf;
pub const PRIMARY_WASH: u32 = 0xfff2eb;
pub const BORDER: u32 = 0xf5f5f6;
pub const FIELD: u32 = 0xf6f6f6;
pub const PANEL_TOP: f32 = 20.;
pub const INSPECTOR_TAB_HEIGHT: f32 = 40.;
pub const PANEL_RADIUS: f32 = 18.;
pub const CONTROL_RADIUS: f32 = 10.;
pub const DETAIL_RADIUS: f32 = 6.;
pub const SELECTION_RADIUS: f32 = 4.;
pub(crate) const CHAT_SECTION_PADDING_X: Pixels = px(15.);
pub(crate) const CHAT_SECTION_PADDING_Y: Pixels = px(11.);
pub(crate) const CHAT_SECTION_HEADER_HEIGHT: Pixels = px(32.);
pub(crate) const INSPECTOR_SECTION_PADDING: Pixels = px(14.);
pub(crate) const INSPECTOR_SECTION_GAP: Pixels = px(12.);

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

pub fn timeline_time(time: impl Into<SharedString>, alpha: f32) -> Div {
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

pub fn chat_card() -> Div {
    card()
        .rounded(px(0.))
        .bg(hsla(0., 0., 0., 0.))
        .border_color(hsla(0., 0., 0., 0.))
        .shadow(Vec::<BoxShadow>::new())
        .child(
            canvas(
                |_, _, _| (),
                |bounds, (), window, _| {
                    let radius = limited_radius(
                        PANEL_RADIUS,
                        bounds.size.width.min(bounds.size.height).into(),
                    );
                    let corners = Corners::all(radius);
                    window.paint_drop_shadows(
                        bounds,
                        corners,
                        &[BoxShadow {
                            inset: false,
                            color: rgba(0x00000002).into(),
                            offset: point(px(0.), px(1.)),
                            blur_radius: px(6.),
                            spread_radius: px(0.),
                        }],
                    );
                    window.paint_quad(quad(
                        bounds,
                        corners,
                        rgb(BACKGROUND),
                        Edges::all(px(1.)),
                        rgb(BORDER),
                        BorderStyle::default(),
                    ));
                },
            )
            .absolute()
            .inset_0(),
        )
}

pub fn section_marker() -> Div {
    div()
        .w(px(2.5))
        .h(px(14.))
        .flex_shrink_0()
        .rounded_full()
        .bg(rgb(PRIMARY))
}

pub fn inspector_section(header: Div) -> Div {
    card()
        .p(INSPECTOR_SECTION_PADDING)
        .gap(INSPECTOR_SECTION_GAP)
        .child(
            row()
                .w_full()
                .gap(px(8.))
                .child(section_marker())
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
        .bg(fade_gradient(1.))
}

pub fn fade_gradient(opacity: f32) -> Background {
    linear_gradient(
        180.,
        linear_color_stop(hsla(0., 0., 0.992, 0.), 0.),
        linear_color_stop(hsla(0., 0., 0.992, opacity), 1.),
    )
}

fn usage_fraction(cost: f64, budget: f64) -> f32 {
    if !cost.is_finite() || !budget.is_finite() || budget <= 0. {
        return 0.;
    }
    (cost / budget).clamp(0., 1.) as f32
}

pub fn usage_ring(usage: Option<(f64, f64)>) -> Div {
    const SIZE: f32 = 12.;
    let fraction = usage
        .map(|(cost, budget)| usage_fraction(cost, budget))
        .unwrap_or(0.);
    div()
        .relative()
        .size(px(SIZE))
        .child(icon("usage-track", SIZE, PROGRESS_TRACK))
        .child(
            canvas(
                |_, _, _| (),
                move |bounds, (), window, _| {
                    if fraction == 0. {
                        return;
                    }
                    let center = bounds.center();
                    let position = |portion: f32| {
                        let angle = -std::f32::consts::FRAC_PI_2 + std::f32::consts::TAU * portion;
                        center + point(px(SIZE * 0.4 * angle.cos()), px(SIZE * 0.4 * angle.sin()))
                    };
                    let mut path = PathBuilder::stroke(px(1.8));
                    path.move_to(position(0.));
                    let steps = (fraction * 64.).ceil() as usize;
                    for step in 1..=steps {
                        path.line_to(position(fraction * step as f32 / steps as f32));
                    }
                    if let Ok(path) = path.build() {
                        window.paint_path(path, rgb(PRIMARY));
                    }
                    for end in [0., fraction] {
                        let mut cap = fill(
                            Bounds::new(
                                position(end) - point(px(0.9), px(0.9)),
                                size(px(1.8), px(1.8)),
                            ),
                            rgb(PRIMARY),
                        );
                        cap.corner_radii = Corners::all(px(0.9));
                        window.paint_quad(cap);
                    }
                },
            )
            .absolute()
            .inset_0(),
        )
}

#[cfg(test)]
mod tests {
    use super::{column_widths, limited_radius, usage_fraction};
    use gpui_kit::px;

    #[test]
    fn daily_budget_ring_clamps_actual_spend() {
        assert_eq!(usage_fraction(0., 1.), 0.);
        assert!((usage_fraction(0.11, 1.) - 0.11).abs() < 0.0001);
        assert_eq!(usage_fraction(0.5, 2.), 0.25);
        assert_eq!(usage_fraction(1., 1.), 1.);
        assert_eq!(usage_fraction(2., 1.), 1.);
        assert_eq!(usage_fraction(f64::NAN, 1.), 0.);
        assert_eq!(usage_fraction(1., 0.), 0.);
    }

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
