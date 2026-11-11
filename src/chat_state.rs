use crate::chat_data::{Opened, RequestError, Summary};

#[derive(Debug, Default, PartialEq, Eq)]
pub enum ListPhase {
    #[default]
    Idle,
    Loading,
    Ready,
    Empty,
    Refreshing,
    LoadFailed(String),
    RefreshFailed(String),
    Creating,
    CreateFailed(String),
    Reconciling(String),
}

impl ListPhase {
    pub fn pending(&self) -> bool {
        matches!(self, Self::Loading | Self::Refreshing | Self::Creating)
    }

    pub fn needs_reload(&self) -> bool {
        matches!(self, Self::RefreshFailed(_) | Self::Reconciling(_))
    }
}

#[derive(Debug, Default)]
pub enum HistoryPhase {
    #[default]
    NoSelection,
    Loading,
    Showing(Opened),
    Empty(Opened),
    Refreshing(Opened),
    LoadFailed,
    RefreshFailed(Opened),
    NotFound,
}

impl HistoryPhase {
    pub fn visible(&self) -> Option<&Opened> {
        match self {
            Self::Showing(chat)
            | Self::Empty(chat)
            | Self::Refreshing(chat)
            | Self::RefreshFailed(chat) => Some(chat),
            _ => None,
        }
    }
}

#[derive(Debug, Default)]
pub struct Chats {
    pub rows: Vec<Summary>,
    pub list: ListPhase,
    pub history: HistoryPhase,
    pub selected: Option<String>,
    request: u64,
    unresolved_create: bool,
}

impl Chats {
    pub fn begin_list(&mut self) -> bool {
        if self.list.pending() {
            return false;
        }
        self.list = if self.rows.is_empty() && !self.unresolved_create {
            ListPhase::Loading
        } else {
            ListPhase::Refreshing
        };
        true
    }

    pub fn complete_list(&mut self, result: Result<Vec<Summary>, RequestError>) -> bool {
        let mut selection_removed = false;
        self.list = match result {
            Ok(rows) => {
                if self
                    .selected
                    .as_ref()
                    .is_some_and(|id| !rows.iter().any(|row| &row.conversation_id == id))
                {
                    selection_removed = !matches!(self.history, HistoryPhase::NotFound);
                    self.selected = None;
                    self.history = HistoryPhase::NoSelection;
                    self.request += 1;
                }
                self.rows = rows;
                self.unresolved_create = false;
                self.list_phase()
            }
            Err(error) if self.unresolved_create => ListPhase::Reconciling(error.message().into()),
            Err(error) if self.rows.is_empty() => ListPhase::LoadFailed(error.message().into()),
            Err(error) => ListPhase::RefreshFailed(error.message().into()),
        };
        selection_removed
    }

    pub fn can_create(&self) -> bool {
        matches!(
            self.list,
            ListPhase::Ready | ListPhase::Empty | ListPhase::CreateFailed(_)
        )
    }

    pub fn begin_create(&mut self) -> bool {
        if !self.can_create() {
            return false;
        }
        self.list = ListPhase::Creating;
        true
    }

    pub fn complete_create(&mut self, result: Result<Opened, RequestError>) {
        match result {
            Ok(chat) => {
                self.rows.insert(
                    0,
                    Summary {
                        conversation_id: chat.conversation_id.clone(),
                        work_path: chat.work_path.clone(),
                        title: chat.title.clone(),
                        created_at: chat.updated_at.clone(),
                        updated_at: chat.updated_at.clone(),
                    },
                );
                self.list = self.list_phase();
                self.select(chat.conversation_id.clone(), false);
                self.history = HistoryPhase::Empty(chat);
            }
            Err(RequestError::Transport(error)) => {
                self.unresolved_create = true;
                self.list = ListPhase::Reconciling(error);
            }
            Err(error) => self.list = ListPhase::CreateFailed(error.message().into()),
        }
    }

    fn list_phase(&self) -> ListPhase {
        if self.rows.is_empty() {
            ListPhase::Empty
        } else {
            ListPhase::Ready
        }
    }

    pub fn select(&mut self, id: String, refresh: bool) -> u64 {
        self.request += 1;
        let keep = refresh && self.selected.as_ref() == Some(&id);
        self.selected = Some(id);
        self.history = match std::mem::take(&mut self.history) {
            HistoryPhase::Showing(chat) | HistoryPhase::RefreshFailed(chat) if keep => {
                HistoryPhase::Refreshing(chat)
            }
            _ => HistoryPhase::Loading,
        };
        self.request
    }

    pub fn complete_history(
        &mut self,
        request: u64,
        id: &str,
        result: &Result<Opened, RequestError>,
    ) -> bool {
        if request != self.request || self.selected.as_deref() != Some(id) {
            return false;
        }
        if let Ok(chat) = result {
            if let Some(row) = self.rows.iter_mut().find(|row| row.conversation_id == id) {
                row.work_path.clone_from(&chat.work_path);
                if row.title.is_none() {
                    row.title.clone_from(&chat.title);
                }
            }
        }
        true
    }

    pub fn is_loading_history(&self, request: u64) -> bool {
        self.request == request && matches!(self.history, HistoryPhase::Loading)
    }

    pub fn set_history(&mut self, result: Result<Opened, RequestError>) {
        let previous = std::mem::take(&mut self.history);
        self.history = match (result, previous) {
            (Ok(chat), _) if chat.history.is_empty() => HistoryPhase::Empty(chat),
            (Ok(chat), _) => HistoryPhase::Showing(chat),
            (Err(RequestError::NotFound(_)), _) => HistoryPhase::NotFound,
            (Err(_), HistoryPhase::Refreshing(chat)) => HistoryPhase::RefreshFailed(chat),
            (Err(_), _) => HistoryPhase::LoadFailed,
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty(id: &str) -> Opened {
        Opened {
            workspace: None,
            conversation_id: id.into(),
            work_path: None,
            history: vec![],
            title: None,
            updated_at: None,
            approvals: Default::default(),
            run_seconds: Default::default(),
        }
    }

    #[test]
    fn late_history_does_not_replace_selection_or_new_chat() {
        let mut chats = Chats::default();
        let first = chats.select("a".into(), false);
        let second = chats.select("b".into(), false);
        assert!(!chats.complete_history(first, "a", &Ok(empty("a"))));
        chats.complete_create(Ok(empty("new")));
        assert!(!chats.complete_history(second, "b", &Ok(empty("b"))));
        assert!(matches!(chats.history, HistoryPhase::Empty(_)));
    }

    #[test]
    fn uncertain_create_blocks_duplicate_until_successful_reload() {
        let mut chats = Chats::default();
        chats.complete_list(Ok(vec![]));
        assert!(chats.begin_create());
        chats.complete_create(Err(RequestError::Transport("lost response".into())));
        assert!(!chats.begin_create());
        assert!(chats.begin_list());
        chats.complete_list(Err(RequestError::Transport("still unavailable".into())));
        assert!(!chats.begin_create());
        assert!(chats.begin_list());
        chats.complete_list(Ok(vec![]));
        assert_eq!(chats.list, ListPhase::Empty);
        assert!(chats.begin_create());
    }

    #[test]
    fn list_and_history_keep_cached_data_on_refresh_failure() {
        let mut chats = Chats::default();
        assert!(chats.begin_list());
        chats.complete_list(Err(RequestError::Rejected("storage unavailable".into())));
        assert!(matches!(chats.list, ListPhase::LoadFailed(_)));
        assert!(chats.begin_list());
        chats.complete_list(Ok(vec![Summary {
            conversation_id: "a".into(),
            title: None,
            created_at: None,
            work_path: None,
            updated_at: None,
        }]));
        assert_eq!(chats.list, ListPhase::Ready);
        assert!(chats.begin_list());
        chats.complete_list(Err(RequestError::Rejected("storage unavailable".into())));
        assert!(matches!(chats.list, ListPhase::RefreshFailed(_)));
        assert_eq!(chats.rows.len(), 1);

        chats.select("a".into(), false);
        chats.set_history(Err(RequestError::Rejected("snapshot unavailable".into())));
        assert!(matches!(chats.history, HistoryPhase::LoadFailed));
        chats.select("a".into(), true);
        let mut opened = empty("a");
        opened.history.push(crate::chat_data::Message {
            parts: vec![],
            run_id: None,
        });
        chats.set_history(Ok(opened));
        chats.select("a".into(), true);
        chats.set_history(Err(RequestError::Rejected("snapshot unavailable".into())));
        assert!(matches!(chats.history, HistoryPhase::RefreshFailed(_)));
        assert!(chats.history.visible().is_some());
        chats.select("a".into(), true);
        chats.set_history(Err(RequestError::NotFound("removed".into())));
        assert!(matches!(chats.history, HistoryPhase::NotFound));
        assert!(chats.history.visible().is_none());
    }

    #[test]
    fn list_removal_invalidates_a_pending_history_response() {
        let mut chats = Chats::default();
        let request = chats.select("removed".into(), false);
        assert!(chats.complete_list(Ok(vec![])));
        assert!(!chats.complete_history(request, "removed", &Ok(empty("removed"))));
        assert!(chats.selected.is_none());
        assert!(matches!(chats.history, HistoryPhase::NoSelection));
    }

    #[test]
    fn unbound_title_uses_creation_minute_then_loaded_history_title() {
        let mut chats = Chats::default();
        let mut chat = empty("a");
        let now = chrono::Local::now();
        chat.updated_at = Some(now.to_rfc3339());
        chats.complete_create(Ok(chat));
        assert_eq!(
            chats.rows[0].title(),
            now.format("%Y-%m-%d %H:%M").to_string()
        );

        let request = chats.select("a".into(), false);
        let mut opened = empty("a");
        opened.title = Some("整理会议纪要".into());
        assert!(chats.complete_history(request, "a", &Ok(opened)));
        assert_eq!(chats.rows[0].title(), "整理会议纪要");
    }
}
