use crate::{text_fields, theme::*};
use gpui_kit::{
    base::input::{InputContent, Textarea, TextareaState},
    *,
};
use std::hash::{DefaultHasher, Hash, Hasher};

fn content(text: &str) -> (InputContent, String, usize) {
    let mut content = InputContent::new(text.to_owned());
    let mut display = String::new();
    let mut offset = 0;
    let mut count = 0;
    while let Some((start, prefix, folder, closing)) = [
        ("［文件引用：", false, "］"),
        ("［目录引用：", true, "］"),
        ("[文件引用：", false, "]"),
        ("[目录引用：", true, "]"),
    ]
    .into_iter()
    .filter_map(|(prefix, folder, closing)| {
        text[offset..]
            .find(prefix)
            .map(|index| (offset + index, prefix, folder, closing))
    })
    .min_by_key(|(index, ..)| *index)
    {
        let json_start = start + prefix.len();
        let mut stream =
            serde_json::Deserializer::from_str(&text[json_start..]).into_iter::<String>();
        let Some(Ok(path)) = stream.next() else {
            display.push_str(&text[offset..json_start]);
            offset = json_start;
            continue;
        };
        let json_end = json_start + stream.byte_offset();
        if !text[json_end..].starts_with(closing) {
            display.push_str(&text[offset..json_end]);
            offset = json_end;
            continue;
        }
        let end = json_end + closing.len();
        let token = text_fields::path_reference(&path, folder);
        display.push_str(&text[offset..start]);
        // Keep the exact source range so copying and sending preserve the original reference.
        let token = gpui_kit::base::input::InlineToken::new(
            token.id().clone(),
            text[start..end].to_owned(),
        )
        .with_label(token.label().clone());
        match content.clone().with_token(start..end, token.clone()) {
            Ok(next) => {
                content = next;
                display.push_str(token.label());
                count += 1;
            }
            Err(_) => display.push_str(&text[start..end]),
        }
        offset = end;
    }
    display.push_str(&text[offset..]);
    (content, display, count)
}

#[derive(IntoElement)]
pub struct MessageReferences {
    pub id: SharedString,
    pub text: SharedString,
    pub max_width: f32,
}

impl RenderOnce for MessageReferences {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let (content, display, count) = content(&self.text);
        if count == 0 {
            return label(self.text, 14.).into_any_element();
        }
        let mut hash = DefaultHasher::new();
        self.id.hash(&mut hash);
        self.text.hash(&mut hash);
        let state = window.use_keyed_state(
            SharedString::from(format!("message-reference-{}", hash.finish())),
            cx,
            |window, cx| {
                let mut state = TextareaState::new(window, cx).auto_grow(1, usize::MAX);
                state.set_value(content, window, cx);
                state.set_editor_style(text_fields::editor_style());
                state.set_readonly(true, cx);
                state
            },
        );
        let width = display
            .lines()
            .map(|line| {
                window
                    .text_system()
                    .shape_line(
                        line.to_owned().into(),
                        px(14.),
                        &[TextRun {
                            len: line.len(),
                            font: body_font(),
                            color: rgb(INK).into(),
                            background_color: None,
                            underline: None,
                            strikethrough: None,
                        }],
                        None,
                    )
                    .width
            })
            .max()
            .unwrap_or(px(0.));
        div()
            .w((width + px(count as f32 * 14. + 2.)).min(px(self.max_width.max(1.))))
            .text_size(px(14.))
            .line_height(px(24.))
            .child(Textarea::new(&state).token(|token, _, _| text_fields::reference_label(token)))
            .into_any_element()
    }
}

#[cfg(test)]
mod tests {
    use super::content;
    #[test]
    fn references_restore_tokens_without_changing_source_text() {
        let text = "请看［文件引用：\"/project/文件.rs\"］和［目录引用：\"/project/src\"］";
        let (content, display, count) = content(text);
        assert_eq!(count, 2);
        assert_eq!(content.tokens().len(), 2);
        assert_eq!(display, "请看文件.rs和src");
        assert_eq!(
            content.tokens()[0].token().text().as_ref(),
            "［文件引用：\"/project/文件.rs\"］"
        );
        assert_eq!(super::content("［文件引用：invalid］").2, 0);
        assert_eq!(super::content("ordinary text").1, "ordinary text");
    }
}
