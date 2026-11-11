use std::{borrow::Cow, collections::HashMap};

use chrono::{DateTime, Local};

use serde::{Deserialize, de::DeserializeOwned};
use serde_json::{Value, json};

#[derive(Debug, Deserialize)]
pub struct Summary {
    pub conversation_id: String,
    pub work_path: Option<String>,
    pub title: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

impl Summary {
    pub fn title(&self) -> Cow<'_, str> {
        if let Some(title) = &self.title {
            return Cow::Borrowed(title);
        }
        if self.work_path.is_none()
            && let Some(time) = self
                .created_at
                .as_deref()
                .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        {
            return Cow::Owned(
                time.with_timezone(&Local)
                    .format("%Y-%m-%d %H:%M")
                    .to_string(),
            );
        }
        Cow::Borrowed("新聊天")
    }

    pub fn project(&self) -> &str {
        self.work_path
            .as_deref()
            .and_then(|path| std::path::Path::new(path).file_name())
            .and_then(|name| name.to_str())
            .or(self.work_path.as_deref())
            .unwrap_or("其他")
    }
}

#[derive(Debug, Deserialize)]
pub struct Opened {
    pub workspace: Option<String>,
    pub conversation_id: String,
    pub work_path: Option<String>,
    pub history: Vec<Message>,
    pub title: Option<String>,
    pub updated_at: Option<String>,
    #[serde(default)]
    pub approvals: HashMap<String, Approval>,
    #[serde(default)]
    pub run_seconds: HashMap<String, u64>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Approval {
    pub reason: String,
    pub action: String,
}

#[derive(Debug, Deserialize)]
pub struct Message {
    pub run_id: Option<String>,
    pub parts: Vec<Value>,
}

#[derive(Debug)]
pub enum RequestError {
    Rejected(String),
    NotFound(String),
    Transport(String),
}

impl RequestError {
    pub fn message(&self) -> &str {
        match self {
            Self::Rejected(message) | Self::NotFound(message) | Self::Transport(message) => message,
        }
    }
}

pub fn request<T: DeserializeOwned>(command: &str, id: Option<&str>) -> Result<T, RequestError> {
    request_payload(json!({"command": command, "conversation_id": id}))
}

pub fn create(work_path: Option<&str>) -> Result<Opened, RequestError> {
    request_payload(json!({"command": "create", "work_path": work_path}))
}

pub(crate) fn request_payload<T: DeserializeOwned>(payload: Value) -> Result<T, RequestError> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Reply<T> {
        Ok { ok: T },
        Error { error: String, code: Option<String> },
    }
    let transport = |error: anyhow::Error| RequestError::Transport(error.to_string());
    let bridge = crate::agent_bridge::AgentBridge::bundled().map_err(transport)?;
    let response = bridge.request(&payload.to_string()).map_err(transport)?;
    match serde_json::from_str(&response)
        .map_err(|error| RequestError::Transport(error.to_string()))?
    {
        Reply::Ok { ok } => Ok(ok),
        Reply::Error { error, code } if code.as_deref() == Some("not_found") => {
            Err(RequestError::NotFound(error))
        }
        Reply::Error { error, .. } => Err(RequestError::Rejected(error)),
    }
}
