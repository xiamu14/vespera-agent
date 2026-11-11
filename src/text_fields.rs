use crate::theme::*;
use gpui_kit::base::input::{
    DisplayPoint, InlineToken, Input, InputContent, InputEditorStyle, InputState, RopeExt,
    Textarea, TextareaState,
};
use gpui_kit::*;
use std::{sync::LazyLock, time::Instant};

static CARET_EPOCH: LazyLock<Instant> = LazyLock::new(Instant::now);

pub(crate) fn path_reference(path: &str, folder: bool) -> InlineToken {
    let kind = if folder { "目录" } else { "文件" };
    let quoted = serde_json::to_string(path).expect("a string is serializable");
    let name = std::path::Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(path);
    InlineToken::new(format!("path:{path}"), format!("［{kind}引用：{quoted}］"))
        .with_label(name.to_owned())
}

fn caret_scale(seconds: f32) -> f32 {
    let phase = seconds.rem_euclid(1.);
    if phase < 0.7 {
        1.
    } else if phase < 0.85 {
        (1. - (phase - 0.7) / 0.15).powi(4)
    } else {
        1. - (1. - (phase - 0.85) / 0.15).powi(4)
    }
}

pub(crate) fn editor_style() -> InputEditorStyle {
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
                let scale = if cx.reduce_motion() {
                    1.
                } else {
                    caret_scale(CARET_EPOCH.elapsed().as_secs_f32())
                };
                let width = px(2.);
                bounds.size.width = width;
                let height = (bounds.size.height - px(4.)).max(px(0.)) * scale;
                bounds.origin.y += (bounds.size.height - height) / 2.;
                bounds.size.height = height;
                bounds.origin.x =
                    (bounds.origin.x - width / 2.).clamp(viewport.left(), viewport.right() - width);
                let mut color: Hsla = rgb(PRIMARY).into();
                color.a = scale;
                let mut caret = fill(bounds, color);
                caret.corner_radii = Corners::all(width / 2.);
                window.paint_quad(caret);
                if !cx.reduce_motion() {
                    window.request_animation_frame();
                }
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

pub fn input_field(state: &Entity<InputState>) -> Div {
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

pub fn draft(
    window: &mut Window,
    cx: &mut App,
    preview: bool,
    conversation_id: Option<&str>,
) -> Entity<TextareaState> {
    let key = SharedString::from(format!(
        "message-draft-{}",
        conversation_id.unwrap_or_default()
    ));
    let state = window.use_keyed_state(key, cx, |window, cx| {
        let mut state = TextareaState::new(window, cx).rows(2).submit_on_enter(true);
        state.set_editor_style(InputEditorStyle {
            // A zero alpha would restore GPUI's square default selection.
            selection: hsla(0., 0., 0., f32::EPSILON),
            ..editor_style()
        });
        state
    });
    if preview {
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
    }
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
        .child(Textarea::new(state).token(|token, _, _| reference_label(token)))
        .child(caret!(state))
}

pub(crate) fn reference_label(token: &gpui_kit::base::input::InlineTokenContext) -> Div {
    label(token.token().label().clone(), 14.)
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
}

#[cfg(test)]
mod tests {
    use super::{caret_scale, path_reference};

    #[test]
    fn reference_keeps_the_full_path_for_the_agent_and_a_short_label() {
        let path = "/workspace/项目/a \"quoted\".rs";
        let token = path_reference(path, false);
        assert_eq!(token.label().as_ref(), "a \"quoted\".rs");
        assert!(token.text().contains(&serde_json::to_string(path).unwrap()));
        assert!(
            path_reference("/workspace/src", true)
                .text()
                .contains("目录引用")
        );
    }

    #[test]
    fn caret_hides_and_returns_in_150ms() {
        for (seconds, expected) in [
            (0., 1.),
            (0.7, 1.),
            (0.775, 0.0625),
            (0.85, 0.),
            (0.925, 0.9375),
            (1., 1.),
        ] {
            assert!((caret_scale(seconds) - expected).abs() < 0.001);
        }
    }
}
