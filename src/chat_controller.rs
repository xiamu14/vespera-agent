use crate::{
    Vespera,
    chat_data::{Opened, RequestError, Summary, create, request},
    chat_state::{HistoryPhase, ListPhase},
    system_notification::notify,
};
use gpui_kit::*;
use std::time::Duration;

actions!(vespera_chat, [ReloadChats]);

impl Vespera {
    pub(super) fn reload_list(&mut self, cx: &mut Context<Self>) {
        let Some(chats) = &mut self.chats else { return };
        let initial = chats.list == ListPhase::Idle;
        if !chats.begin_list() {
            return;
        }
        cx.notify();
        self.notify_slow_load(
            "正在读取会话列表",
            |this| {
                this.chats
                    .as_ref()
                    .is_some_and(|chats| chats.list == ListPhase::Loading)
            },
            cx,
        );
        let request = cx
            .background_executor()
            .spawn(async { request::<Vec<Summary>>("list", None) });
        cx.spawn(async move |view, cx| {
            let result = request.await;
            let _ = view.update(cx, |this, cx| {
                let chats = this.chats.as_mut().expect("live chat");
                if chats.complete_list(result) {
                    notify("会话已不存在", "");
                }
                match &chats.list {
                    ListPhase::Empty => notify("暂无会话", "使用侧栏的新聊天按钮开始。"),
                    ListPhase::LoadFailed(_) => notify("无法读取会话列表", "按 ⌘R 重新读取。"),
                    ListPhase::Ready if initial => {
                        let id = chats.rows[0].conversation_id.clone();
                        this.open_chat(id, false, cx);
                    }
                    _ => {}
                }
                this.update_search_status();
                cx.notify();
            });
        })
        .detach();
    }

    pub(super) fn new_chat(&mut self, work_path: Option<String>, cx: &mut Context<Self>) -> bool {
        let Some(chats) = &mut self.chats else {
            return false;
        };
        if !chats.begin_create() {
            return false;
        }
        cx.notify();
        let request = cx
            .background_executor()
            .spawn(async move { create(work_path.as_deref()) });
        cx.spawn(async move |view, cx| {
            let result = request.await;
            let _ = view.update(cx, |this, cx| {
                let chats = this.chats.as_mut().expect("live chat");
                this.reset_search = result.is_ok();
                chats.complete_create(result);
                if this.reset_search {
                    this.load_inspector(cx);
                } else if matches!(chats.list, ListPhase::CreateFailed(_)) {
                    notify("无法新建聊天", "可以再次点击新聊天。");
                }
                cx.notify();
            });
        })
        .detach();
        true
    }

    pub(super) fn open_chat(&mut self, id: String, refresh: bool, cx: &mut Context<Self>) {
        let Some(chats) = &mut self.chats else { return };
        let generation = chats.select(id.clone(), refresh);
        cx.notify();
        self.notify_slow_load(
            "正在读取聊天历史",
            move |this| {
                this.chats
                    .as_ref()
                    .is_some_and(|chats| chats.is_loading_history(generation))
            },
            cx,
        );
        let target = id.clone();
        let request = cx
            .background_executor()
            .spawn(async move { request::<Opened>("open", Some(&target)) });
        cx.spawn(async move |view, cx| {
            let result = request.await;
            let _ = view.update(cx, |this, cx| {
                let chats = this.chats.as_mut().expect("live chat");
                if !chats.complete_history(generation, &id, &result) {
                    return;
                }
                let not_found = matches!(result, Err(RequestError::NotFound(_)));
                let refreshed_run = result.as_ref().ok().is_some_and(|chat| {
                    this.live
                        .turns
                        .get(&id)
                        .and_then(|turn| turn.run_id.as_deref())
                        .is_some_and(|run| {
                            chat.history
                                .iter()
                                .any(|message| message.run_id.as_deref() == Some(run))
                        })
                });
                if refreshed_run && this.live.turns.get(&id).is_some_and(|turn| !turn.running) {
                    this.live.turns.remove(&id);
                }
                chats.set_history(result);
                match &chats.history {
                    HistoryPhase::Empty(_) => notify("会话暂无历史", ""),
                    HistoryPhase::NotFound => notify("会话已不存在", "正在重新读取会话列表。"),
                    HistoryPhase::LoadFailed => {
                        notify("无法读取聊天历史", "重新选择该会话可重试。")
                    }
                    _ => {}
                }
                if not_found {
                    this.reload_list(cx);
                } else {
                    this.load_inspector(cx);
                }
                this.update_search_status();
                cx.notify();
            });
        })
        .detach();
    }

    pub(super) fn update_search_status(&mut self) {
        let query = &self.search_query;
        let no_matches = self.chats.as_ref().is_some_and(|chats| {
            !query.is_empty()
                && !chats.rows.is_empty()
                && !chats
                    .rows
                    .iter()
                    .any(|row| crate::sidebar::matches_query(row, query))
        });
        if no_matches && !self.no_search_matches {
            notify("没有匹配的会话", "");
        }
        self.no_search_matches = no_matches;
    }

    fn notify_slow_load(
        &self,
        title: &'static str,
        still_loading: impl FnOnce(&Self) -> bool + 'static,
        cx: &mut Context<Self>,
    ) {
        let timer = cx.background_executor().timer(Duration::from_millis(750));
        cx.spawn(async move |view, cx| {
            timer.await;
            let _ = view.update(cx, |this, _| {
                if still_loading(this) {
                    notify(title, "");
                }
            });
        })
        .detach();
    }

    pub(super) fn reload_chats(&mut self, cx: &mut Context<Self>) {
        let selected = self.chats.as_ref().and_then(|chats| chats.selected.clone());
        self.reload_list(cx);
        if let Some(id) = selected {
            self.open_chat(id, true, cx);
        }
    }
}
