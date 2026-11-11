use serde::Deserialize;
use serde_json::{Value, json};
use std::{collections::HashMap, time::Instant};

#[derive(Default)]
pub struct LiveChat {
    pub turns: HashMap<String, Turn>,
    pub attachments: HashMap<String, Vec<String>>,
}

pub struct Turn {
    pub parts: Vec<Value>,
    pub running: bool,
    pub stopping: bool,
    pub(crate) started: bool,
    pub answering: bool,
    pub question: Option<Question>,
    pub model: Option<String>,
    pub context_used: Option<f64>,
    pub llm_started: Option<Instant>,
    response: Option<(u64, usize)>,
    pub(crate) run_id: Option<String>,
}

#[derive(Clone, Deserialize)]
pub struct Question {
    pub header: String,
    pub question: String,
    pub options: Vec<OptionLabel>,
    pub multi_select: bool,
    #[serde(skip)]
    pub id: u64,
    #[serde(skip)]
    pub details: String,
    #[serde(skip)]
    pub selected: Vec<String>,
}

#[derive(Clone, Deserialize)]
pub struct OptionLabel {
    pub label: String,
    pub description: Option<String>,
}

impl Turn {
    pub(crate) fn new(prompt: &str, files: &[String]) -> Self {
        let content = files.iter().fold(prompt.to_owned(), |mut text, path| {
            if !text.is_empty() {
                text.push('\n');
            }
            text.push_str("［附件］");
            text.push_str(filename(path));
            text
        });
        Self {
            parts: vec![
                json!({"part_kind": "user-prompt", "content": content, "timestamp": chrono::Utc::now().to_rfc3339()}),
            ],
            running: true,
            stopping: false,
            started: false,
            answering: false,
            question: None,
            model: None,
            context_used: None,
            llm_started: None,
            response: None,
            run_id: None,
        }
    }

    pub(crate) fn event(&mut self, event: Value) {
        if event["type"] == "question" {
            if let Ok(mut question) = serde_json::from_value::<Question>(event["question"].clone())
                && let Some(id) = event["question_id"].as_u64()
            {
                question.id = id;
                question.details = event["details"].as_str().unwrap_or_default().to_owned();
                self.question = Some(question);
                self.answering = false;
            }
            return;
        }
        if event["type"] == "delegation" {
            self.delegation(&event["event"]["task"]);
            return;
        }
        if event["type"] != "agent" {
            return;
        }
        if let Some(id) = event["run_id"].as_str() {
            self.run_id = Some(id.to_owned());
        }
        if let Some(model) = event["model"].as_str() {
            self.model = Some(model.to_owned());
        }
        self.context_used = event["context_used"].as_f64().or(self.context_used);
        let body = &event["event"];
        let index = body["index"].as_u64().unwrap_or_default();
        match body["event_kind"].as_str() {
            Some("model_request_started") => {
                self.llm_started.get_or_insert_with(Instant::now);
            }
            Some("part_start") if body["part"]["part_kind"] == "text" => {
                self.response = Some((index, self.parts.len()));
                self.parts.push(body["part"].clone());
            }
            Some("part_delta") if body["delta"]["part_delta_kind"] == "text" => {
                if let Some(text) = body["delta"]["content_delta"].as_str() {
                    if let Some((_, part)) = self.response.filter(|(current, _)| *current == index)
                    {
                        let content = self.parts[part]["content"].as_str().unwrap_or_default();
                        self.parts[part]["content"] = Value::String(format!("{content}{text}"));
                    } else {
                        self.response = Some((index, self.parts.len()));
                        self.parts
                            .push(json!({"part_kind": "text", "content": text}));
                    }
                }
            }
            Some("function_tool_call" | "function_tool_result") => {
                self.response = None;
                if body["event_kind"] == "function_tool_result" {
                    self.question = None;
                }
                self.parts.push(body["part"].clone());
            }
            _ => {}
        }
    }

    fn delegation(&mut self, task: &Value) {
        let Some(id) = task["id"].as_str() else {
            return;
        };
        let key = format!("delegation-{id}");
        let has_call = self
            .parts
            .iter()
            .any(|part| part["tool_call_id"] == key && part["part_kind"] == "tool-call");
        if !has_call {
            self.parts
                .push(json!({"part_kind":"tool-call", "tool_call_id":key,
                "tool_name":task["agent_name"], "args":{"query":task["prompt"]}}));
        }
        if task["status"] == "finished" {
            let outcome = match task["outcome"].as_str() {
                Some("ok") => "success",
                Some("cancelled") => "interrupted",
                _ => "failed",
            };
            let part = json!({"part_kind":"tool-return","tool_call_id":key,"outcome":outcome,"content":task["output"]});
            if let Some(old) = self
                .parts
                .iter_mut()
                .find(|old| old["tool_call_id"] == key && old["part_kind"] == "tool-return")
            {
                *old = part;
            } else {
                self.parts.push(part);
            }
        }
    }
}

pub fn filename(path: &str) -> &str {
    std::path::Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(path)
}

#[cfg(test)]
mod tests {
    use super::Turn;
    use serde_json::json;

    #[test]
    fn stream_keeps_model_text_tools_and_question() {
        let mut turn = Turn::new("hello", &[]);
        turn.event(json!({"type":"agent","run_id":"run", "model":"model",
            "event":{"event_kind":"part_start","index":0,"part":{"part_kind":"text","content":"A"}}}));
        turn.event(
            json!({"type":"agent","event":{"event_kind":"part_delta","index":0,
            "delta":{"part_delta_kind":"text","content_delta":"B"}}}),
        );
        assert_eq!(turn.parts[1]["content"], "AB");
        assert!(turn.llm_started.is_none());
        turn.event(json!({"type":"agent","event":{"event_kind":"model_request_started"}}));
        assert!(turn.llm_started.is_some());
        turn.event(json!({"type":"question", "question_id":1, "question":{
            "header":"Q", "question":"Choose", "multi_select":true, "options":[{"label":"A"}]
        }}));
        assert_eq!(turn.question.as_ref().unwrap().id, 1);
        turn.event(
            json!({"type":"agent","event":{"event_kind":"function_tool_result",
            "part":{"part_kind":"tool-return","tool_call_id":"tool","content":"done"}}}),
        );
        assert!(turn.question.is_none());
        assert_eq!(turn.parts.len(), 3);
        assert_eq!(turn.model.as_deref(), Some("model"));
    }
}
