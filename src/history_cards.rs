use crate::{Vespera, live_state::Question, theme::*};
use gpui_kit::*;
use serde_json::Value;

pub fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or_default()
}

pub fn user_text(part: &Value) -> Option<String> {
    let content = &part["content"];
    let text = if let Some(text) = content.as_str() {
        text.to_owned()
    } else {
        content
            .as_array()?
            .iter()
            .filter_map(|item| {
                if let Some(text) = item.as_str() {
                    return Some(text.to_owned());
                }
                match item["kind"].as_str()? {
                    "text-content" if item["metadata"].is_null() => {
                        item["content"].as_str().map(str::to_owned)
                    }
                    "binary" | "image-url" | "document-url" | "audio-url" | "video-url"
                    | "uploaded-file" => Some("［附件］".into()),
                    _ => None,
                }
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    (!text.trim().is_empty()).then_some(text)
}

pub fn choice(question: &Value, output: &Value, status: &'static str) -> Div {
    let answer = output.get(text(question, "header"));
    let summary = match answer {
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join("、"),
        Some(Value::String(value)) => value.clone(),
        _ => status.to_owned(),
    };
    chat_card()
        .p(px(8.))
        .gap(px(8.))
        .child(label(text(question, "question").to_owned(), 14.).font(emphasis_font()))
        .child(muted(summary, 14.))
}

pub fn question(
    question: &Question,
    conversation_id: &str,
    answering: bool,
    cx: &Context<Vespera>,
) -> impl IntoElement {
    crate::question_card::QuestionCard {
        question: question.clone(),
        conversation_id: conversation_id.to_owned(),
        answering,
        app: cx.entity().downgrade(),
    }
}

#[cfg(test)]
mod tests {
    use super::user_text;
    use serde_json::json;

    #[test]
    fn injected_memory_is_not_an_attachment_but_real_media_is_preserved() {
        assert_eq!(
            user_text(
                &json!({"content":[{"kind":"text-content", "content":"internal memory",
            "metadata":{"origin":"pydantic-ai-harness.memory.v1"}}]})
            ),
            None
        );
        assert_eq!(
            user_text(&json!({"content":["explain", {"kind":"binary", "media_type":"image/png"}]})),
            Some("explain\n［附件］".into())
        );
        assert_eq!(
            user_text(
                &json!({"content":[{"kind":"text-content", "content":"human text", "metadata":null}]})
            ),
            Some("human text".into())
        );
        assert_eq!(user_text(&json!({"content":"  \n"})), None);
        assert_eq!(user_text(&json!({"content":[{"kind":"unknown"}]})), None);
    }
}
