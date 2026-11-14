use chrono::{DateTime, Local};
use gpui_kit::*;
use serde::Deserialize;

use crate::{Vespera, chat_data::request, system_notification::notify};

#[derive(Default)]
pub struct InspectionState {
    generation: u64,
    conversation_id: Option<String>,
    data: Option<Inspection>,
}

#[derive(Default, Deserialize)]
pub struct Inspection {
    pub directory: Vec<DirectoryEntry>,
    pub directory_truncated: bool,
    pub model: Option<String>,
    pub context_used: Option<f64>,
    pub today_cost: Option<f64>,
    pub total_cost: Option<f64>,
    pub daily_budget_usd: f64,
    pub deliveries: Vec<DeliveryGroup>,
    pub user_memory: String,
    pub project_memory: String,
    pub binding_history: Vec<BindingChange>,
}

#[derive(Clone, Deserialize)]
pub struct DirectoryEntry {
    pub path: String,
    pub name: String,
    pub depth: usize,
    pub folder: bool,
}

#[derive(Deserialize)]
pub struct DeliveryGroup {
    pub title: Option<String>,
    pub timestamp: String,
    pub items: Vec<Delivery>,
}

#[derive(Deserialize)]
pub struct Delivery {
    pub path: String,
    pub action: String,
    pub kind: String,
    pub title: Option<String>,
    pub sha: Option<String>,
    pub previous: Option<String>,
    pub mime: Option<String>,
    pub local_path: Option<String>,
    #[serde(default)]
    pub observed: bool,
}

#[derive(Deserialize)]
pub struct BindingChange {
    pub old_path: Option<String>,
    pub new_path: Option<String>,
    pub reason: String,
}

impl InspectionState {
    pub fn daily_usage(&self) -> Option<(f64, f64)> {
        let data = self.data.as_ref()?;
        Some((data.today_cost?, data.daily_budget_usd))
    }

    pub fn data(&self, selection: Option<&str>) -> Option<&Inspection> {
        (self.conversation_id.as_deref() == selection)
            .then_some(self.data.as_ref())
            .flatten()
    }
}

pub fn time_label(value: &str) -> String {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Local).format("%H:%M").to_string())
        .unwrap_or_default()
}

impl Vespera {
    pub(crate) fn load_inspector(&mut self, cx: &mut Context<Self>) {
        let Some(id) = self.chats.as_ref().and_then(|chats| chats.selected.clone()) else {
            return;
        };
        self.inspection.generation += 1;
        let generation = self.inspection.generation;
        let target = id.clone();
        let request = cx
            .background_executor()
            .spawn(async move { request::<Inspection>("inspect", Some(&target)) });
        cx.spawn(async move |view, cx| {
            let result = request.await;
            let _ = view.update(cx, |this, cx| {
                if this.inspection.generation != generation
                    || this
                        .chats
                        .as_ref()
                        .and_then(|chats| chats.selected.as_deref())
                        != Some(&id)
                {
                    return;
                }
                match result {
                    Ok(data) => {
                        this.inspection.conversation_id = Some(id);
                        this.inspection.data = Some(data);
                    }
                    Err(error) => notify("无法读取会话详情", error.message()),
                }
                cx.notify();
            });
        })
        .detach();
    }
}
