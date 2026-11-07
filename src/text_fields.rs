use crate::theme::*;
use gpui_kit::base::input::{
    DisplayPoint, InlineToken, Input, InputContent, InputEditorStyle, InputState, RopeExt,
    Textarea, TextareaState,
};
use gpui_kit::*;
use std::{sync::LazyLock, time::Instant};

static CARET_EPOCH: LazyLock<Instant> = LazyLock::new(Instant::now);

fn caret_breath(seconds: f32) -> f32 {
    let phase = seconds.rem_euclid(1.4) / 0.7;
    let progress = 1. - (phase - 1.).abs();
    // Quintic ease-in-out keeps velocity and acceleration zero at both reversals.
    progress * progress * progress * (progress * (progress * 6. - 15.) + 10.)
}

fn editor_style() -> InputEditorStyle {
    InputEditorStyle {
        foreground: rgb(INK).into(),
        muted_foreground: rgb(MUTED).into(),
        // Keep this nonzero so the input does not resolve it to foreground ink.
        caret: hsla(0., 0., 0., f32::EPSILON),
        ..Default::default()
    }
}

fn capsule(
    bounds: impl FnOnce(&Window, &App) -> Option<Bounds<Pixels>> + 'static,
) -> impl IntoElement {
    canvas(
        |_, _, _| (),
        move |viewport, (), window, cx| {
            if let Some(mut bounds) = bounds(window, cx) {
                let breath = caret_breath(CARET_EPOCH.elapsed().as_secs_f32());
                let width = px(2.);
                bounds.size.width = width;
                bounds.origin.x =
                    (bounds.origin.x - width / 2.).clamp(viewport.left(), viewport.right() - width);
                let mut color: Hsla = rgb(PRIMARY).into();
                color.l += (1. - color.l) * 0.3 * breath;
                let mut caret = fill(bounds, color);
                caret.corner_radii = Corners::all(width / 2.);
                window.paint_quad(caret);
                window.request_animation_frame();
            }
        },
    )
    .absolute()
    .size_full()
}

macro_rules! caret {
    ($state:expr) => {{
        let state = $state.clone();
        capsule(move |window, cx| {
            let state = state.read(cx);
            if !state.focus_handle(cx).is_focused(window) || !window.is_window_active() {
                return None;
            }
            let geometry = state.prepaint_caret_geometry()?;
            let mut bounds = geometry.cursor_bounds();
            bounds.origin.y += geometry.scroll_offset().y;
            Some(bounds)
        })
    }};
}

pub fn search(window: &mut Window, cx: &mut App) -> Entity<InputState> {
    let state = window.use_keyed_state("history-query", cx, |window, cx| {
        let mut state = InputState::new(window, cx).placeholder("搜索历史");
        state.set_editor_style(editor_style());
        state
    });
    let focused = state.read(cx).focus_handle(cx).is_focused(window);
    let placeholder = if focused { "" } else { "搜索历史" };
    let previous = window.use_keyed_state("search-focus", cx, |_, _| false);
    if *previous.read(cx) != focused {
        previous.update(cx, |previous, _| *previous = focused);
        state.update(cx, |state, cx| {
            state.set_placeholder(placeholder, window, cx)
        });
    }
    state
}

pub fn search_field(state: &Entity<InputState>) -> Div {
    let focus_state = state.clone();
    div()
        .on_mouse_down_out(move |_, window, cx| {
            if focus_state.read(cx).focus_handle(cx).is_focused(window) {
                window.blur(cx);
            }
        })
        .relative()
        .flex_1()
        .min_w_0()
        .overflow_hidden()
        .text_size(px(14.))
        .font_weight(FontWeight::NORMAL)
        .line_height(px(24.))
        .child(Input::new(state))
        .child(caret!(state))
}

pub fn draft(window: &mut Window, cx: &mut App) -> Entity<TextareaState> {
    let state = window.use_keyed_state("message-draft", cx, |window, cx| {
        let mut state = TextareaState::new(window, cx).rows(2);
        state.set_editor_style(InputEditorStyle {
            // A zero alpha would restore GPUI's square default selection.
            selection: hsla(0., 0., 0., f32::EPSILON),
            ..editor_style()
        });
        state
    });
    window.use_keyed_state("seed-message-draft", cx, |window, cx| {
        let prefix = "使用 ";
        let attachment = "界面截图";
        let text = format!("{prefix}{attachment} ");
        let content = InputContent::new(text)
            .with_token(
                prefix.len()..prefix.len() + attachment.len(),
                InlineToken::new("screenshot", attachment),
            )
            .expect("the fixture token matches its text");
        state.update(cx, |state, cx| state.set_value(content, window, cx));
    });
    state
}

fn draft_selection(state: Entity<TextareaState>) -> impl IntoElement {
    canvas(
        |_, _, _| (),
        move |viewport, (), window, cx| {
            let state = state.read(cx);
            let selection = state.selected_range();
            if selection.is_empty()
                || !window.is_window_active()
                || !state.has_selection_focus(window, cx)
            {
                return;
            }
            let Some(line_height) = state.line_height() else {
                return;
            };
            let map = state.display_map();
            let text = state.text();
            let first = (-state.scroll_offset().y / line_height).floor() as usize;
            let last = first + (viewport.size.height / line_height).ceil() as usize + 1;
            for row in first..last.min(map.display_row_count()) {
                let start = map.display_pos_to_buffer_pos(DisplayPoint::new(row, 0));
                let end = map.display_pos_to_buffer_pos(DisplayPoint::new(row, text.len()));
                let row_start = text.line_start_offset(start.line) + start.col;
                let row_end = text.line_start_offset(end.line) + end.col;
                let line_end = text.line_end_offset(start.line);
                let newline =
                    row_end == line_end && selection.end > line_end && line_end < text.len();
                let start = selection.start.max(row_start);
                let end = selection.end.min(row_end);
                if start > end || (start == end && !newline) {
                    continue;
                }
                let Some(bounds) = state.range_to_bounds(&(start..end)) else {
                    continue;
                };
                // shortcut: a selection reaching a soft wrap fills to the viewport edge until GPUI exposes trailing-affinity bounds.
                let right = if bounds.size.height > line_height {
                    viewport.right()
                } else {
                    bounds.right() + if newline { px(4.) } else { px(0.) }
                };
                let bounds = Bounds::from_corners(
                    bounds.origin,
                    point(right, bounds.origin.y + line_height),
                )
                .intersect(&viewport);
                if bounds.size.width > px(0.) && bounds.size.height > px(0.) {
                    let mut highlight = fill(bounds, rgb(PRIMARY_WASH));
                    highlight.corner_radii = Corners::all(px(SELECTION_RADIUS));
                    window.paint_quad(highlight);
                }
            }
        },
    )
    .absolute()
    .size_full()
}

pub fn draft_field(state: &Entity<TextareaState>) -> Div {
    let focus_state = state.clone();
    div()
        .on_mouse_down_out(move |_, window, cx| {
            if focus_state.read(cx).focus_handle(cx).is_focused(window) {
                window.blur(cx);
            }
        })
        .relative()
        .w_full()
        .h(px(48.))
        .overflow_hidden()
        .text_size(px(14.))
        .line_height(px(24.))
        .child(draft_selection(state.clone()))
        .child(Textarea::new(state).token(|token, _, _| {
            label("界面截图", 14.)
                .px(px(7.))
                .py(px(2.))
                .rounded(px(if token.is_selected() {
                    SELECTION_RADIUS
                } else {
                    DETAIL_RADIUS
                }))
                .bg(rgb(if token.is_selected() {
                    PRIMARY_WASH
                } else {
                    0xfff9e9
                }))
                .text_color(rgb(0xbb731c))
        }))
        .child(caret!(state))
}

#[cfg(test)]
mod tests {
    use super::caret_breath;

    #[test]
    fn caret_breathes_from_primary_toward_brighter_and_back() {
        assert!(caret_breath(0.).abs() < 0.001);
        assert!((caret_breath(0.7) - 1.).abs() < 0.001);
        assert!(caret_breath(1.4).abs() < 0.001);
        assert!(caret_breath(0.175) < 0.25);
        assert!(caret_breath(0.525) > 0.75);
        assert!((caret_breath(0.175) - caret_breath(1.225)).abs() < 0.001);
    }
}
