use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

#[derive(Default)]
pub struct LiveChat {
    pub turns: HashMap<String, Turn>,
    pub tasks: HashMap<String, crate::background_tasks::BackgroundTasks>,
    pub attachments: HashMap<String, Vec<String>>,
    pub model_errors: HashMap<String, String>,
}

pub struct Turn {
    pub parts: Vec<Value>,
    pub running: bool,
    pub stopping: bool,
    pub(crate) send_after_stop: bool,
    pub(crate) started: bool,
    pub answering: bool,
    pub question: Option<Question>,
    pub model_failure: Option<ModelFailure>,
    pub model: Option<String>,
    pub context_used: Option<f64>,
    pub llm_started: Option<Instant>,
    paused_at: Option<Instant>,
    paused: Duration,
    response: Option<(u64, usize)>,
    pub(crate) run_id: Option<String>,
    run_ids: HashSet<String>,
}

#[derive(Deserialize)]
pub struct ModelFailure {
    pub token: String,
    pub message: String,
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
}

impl Turn {
    pub(crate) fn bind_run(&mut self, id: &str) {
        self.run_id = Some(id.to_owned());
        self.run_ids.insert(id.to_owned());
    }

    pub(crate) fn owns_message(&self, message: &crate::chat_data::Message) -> bool {
        message
            .run_id
            .as_deref()
            .is_some_and(|run_id| self.run_ids.contains(run_id))
    }

    pub(crate) fn saved_in(&self, history: &[crate::chat_data::Message]) -> bool {
        self.run_id.as_deref().is_some_and(|run_id| {
            history
                .iter()
                .any(|message| message.run_id.as_deref() == Some(run_id))
        })
    }

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
            send_after_stop: false,
            started: false,
            answering: false,
            question: None,
            model_failure: None,
            model: None,
            context_used: None,
            llm_started: None,
            paused_at: None,
            paused: Duration::ZERO,
            response: None,
            run_id: None,
            run_ids: HashSet::new(),
        }
    }

    pub(crate) fn elapsed_seconds(&self) -> u64 {
        self.llm_started.map_or(0, |start| {
            self.paused_at
                .unwrap_or_else(Instant::now)
                .saturating_duration_since(start)
                .saturating_sub(self.paused)
                .as_secs()
        })
    }

    pub(crate) fn timer_running(&self) -> bool {
        self.running && self.paused_at.is_none()
    }

    pub(crate) fn composer_busy(&self) -> bool {
        self.running && self.model_failure.is_none()
    }

    pub(crate) fn event(&mut self, event: Value) {
        if event["type"] == "model_failure" {
            if let Ok(failure) = serde_json::from_value::<ModelFailure>(event) {
                self.model_failure = Some(failure);
                self.paused_at.get_or_insert_with(Instant::now);
            }
            return;
        }
        if event["type"] == "model_retrying" {
            if self
                .model_failure
                .as_ref()
                .is_some_and(|failure| event["token"] == failure.token)
            {
                self.model_failure = None;
                if let Some(start) = self.paused_at.take() {
                    self.paused += start.elapsed();
                }
            }
            return;
        }
        if event["type"] == "run_started" {
            if let Some(id) = event["run_id"].as_str() {
                self.bind_run(id);
            }
            return;
        }
        if event["type"] == "user_wait_finished" {
            if let Some(start) = self.paused_at.take() {
                self.paused += start.elapsed();
            }
            return;
        }
        if event["type"] == "question" {
            if let Ok(mut question) = serde_json::from_value::<Question>(event["question"].clone())
                && let Some(id) = event["question_id"].as_u64()
            {
                if self.llm_started.is_some() {
                    self.paused_at.get_or_insert_with(Instant::now);
                }
                question.id = id;
                question.details = event["details"].as_str().unwrap_or_default().to_owned();
                self.question = Some(question);
                self.answering = false;
            }
            return;
        }
        if event["type"] != "agent" {
            return;
        }
        if let Some(id) = event["run_id"].as_str() {
            self.bind_run(id);
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
    use crate::chat_data::Message;
    use serde_json::json;

    #[test]
    fn model_failure_pauses_timer_until_matching_retry() {
        let mut turn = Turn::new("继续", &[]);
        assert!(turn.composer_busy());
        turn.event(json!({"type":"model_failure", "token":"current", "message":"HTTP 403"}));
        assert!(!turn.composer_busy());
        assert!(!turn.timer_running());
        turn.event(json!({"type":"model_retrying", "token":"stale"}));
        assert!(turn.model_failure.is_some());
        assert!(!turn.composer_busy());
        turn.event(json!({"type":"model_retrying", "token":"current"}));
        assert!(turn.model_failure.is_none());
        assert!(turn.timer_running());
        assert!(turn.composer_busy());
        assert_eq!(turn.parts.len(), 1);
    }

    #[test]
    fn early_run_identity_reconciles_a_user_only_failed_snapshot() {
        let mut turn = Turn::new("继续", &[]);
        turn.bind_run("new-run");
        assert_eq!(turn.run_id.as_deref(), Some("new-run"));
        assert!(turn.llm_started.is_none());
        let older = Message {
            run_id: Some("old-run".into()),
            parts: vec![json!({"part_kind":"user-prompt", "content":"继续"})],
        };
        let saved = Message {
            run_id: Some("new-run".into()),
            parts: vec![json!({"part_kind":"user-prompt", "content":"继续"})],
        };
        assert!(!turn.saved_in(std::slice::from_ref(&older)));
        assert!(turn.saved_in(std::slice::from_ref(&saved)));
        assert!(!turn.owns_message(&older));
        assert!(turn.owns_message(&saved));
        let history = [older, saved];
        assert_eq!(
            history
                .iter()
                .filter(|message| !turn.owns_message(message))
                .count()
                + turn.parts.len(),
            2
        );
        turn.running = false;
        assert!(turn.saved_in(&history));
    }

    #[test]
    fn followup_runs_keep_all_streamed_message_owners() {
        let mut turn = Turn::new("start", &[]);
        turn.event(json!({"type":"run_started", "run_id":"first"}));
        turn.event(json!({"type":"run_started", "run_id":"followup"}));
        let first = Message {
            run_id: Some("first".into()),
            parts: vec![],
        };
        assert!(turn.owns_message(&first));
        assert!(!turn.saved_in(std::slice::from_ref(&first)));
        let followup = Message {
            run_id: Some("followup".into()),
            parts: vec![],
        };
        assert!(turn.owns_message(&followup));
        assert!(turn.saved_in(&[first, followup]));
    }

    #[test]
    fn user_wait_freezes_then_resumes_elapsed_time() {
        use std::time::{Duration, Instant};
        let now = Instant::now();
        let mut turn = Turn::new("hello", &[]);
        turn.llm_started = Some(now - Duration::from_secs(10));
        turn.paused_at = Some(now - Duration::from_secs(5));
        assert_eq!(turn.elapsed_seconds(), 5);
        assert!(!turn.timer_running());
        turn.event(json!({"type":"user_wait_finished"}));
        assert_eq!(turn.elapsed_seconds(), 5);
        assert!(turn.timer_running());
        turn.event(json!({"type":"user_wait_finished"}));
        assert_eq!(turn.elapsed_seconds(), 5);
    }

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
