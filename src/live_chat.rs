use std::time::Duration;

use gpui_kit::{base::input::TextareaState, *};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{
    Vespera,
    chat_data::{Opened, RequestError, request, request_payload},
    system_notification::notify,
};

pub use crate::live_state::{LiveChat, Turn, filename};

const ANSWER_FEEDBACK: Duration = Duration::from_millis(220);

#[derive(Deserialize)]
struct Poll {
    events: Vec<Value>,
    done: bool,
    error: Option<String>,
    model_error: Option<String>,
}

#[derive(Deserialize)]
struct SendReceipt {
    run_id: String,
}

impl Vespera {
    pub(crate) fn selected_turn(&self) -> Option<&Turn> {
        self.live.turns.get(self.chats.as_ref()?.selected.as_ref()?)
    }

    pub(crate) fn selected_files(&self) -> &[String] {
        self.chats
            .as_ref()
            .and_then(|chats| chats.selected.as_ref())
            .and_then(|id| self.live.attachments.get(id))
            .map(Vec::as_slice)
            .unwrap_or_default()
    }

    pub(crate) fn choose_attachments(&mut self, cx: &mut Context<Self>) {
        let Some(id) = self.chats.as_ref().and_then(|chats| chats.selected.clone()) else {
            return;
        };
        let selected = cx.prompt_for_paths(PathPromptOptions {
            files: true,
            directories: false,
            multiple: true,
            prompt: Some("选择图片或 PDF 附件".into()),
        });
        cx.spawn(async move |view, cx| {
            let result = selected.await;
            let _ = view.update(cx, |this, cx| match result {
                Ok(Ok(Some(paths))) => {
                    let files = this.live.attachments.entry(id).or_default();
                    for path in paths {
                        let Some(path) = path.to_str() else {
                            notify("无法添加附件", "文件路径包含无法保存的字符。");
                            continue;
                        };
                        if files.iter().any(|file| file == path) {
                            continue;
                        }
                        if files.len() >= 4 {
                            notify("无法添加更多附件", "每条消息最多添加 4 个附件。");
                            break;
                        }
                        files.push(path.to_owned());
                    }
                    cx.notify();
                }
                Ok(Ok(None)) => {}
                _ => notify("无法打开文件选择器", "请重试。"),
            });
        })
        .detach();
    }

    pub(crate) fn remove_attachment(&mut self, path: &str, cx: &mut Context<Self>) {
        if let Some(id) = self
            .chats
            .as_ref()
            .and_then(|chats| chats.selected.as_ref())
            && let Some(files) = self.live.attachments.get_mut(id)
        {
            files.retain(|file| file != path);
            cx.notify();
        }
    }

    pub(crate) fn submit_draft(
        &mut self,
        draft: &Entity<TextareaState>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(chats) = &self.chats else { return };
        let Some(id) = chats.selected.clone() else {
            return;
        };
        let prompt = draft.read(cx).value().trim().to_owned();
        if chats.history.visible().is_none()
            || (prompt.is_empty() && self.selected_files().is_empty())
        {
            return;
        }
        if let Some(turn) = self.live.turns.get_mut(&id).filter(|turn| turn.running) {
            if !turn.composer_busy() && !turn.stopping {
                turn.send_after_stop = true;
                self.stop_turn(cx);
            }
            return;
        }
        if self
            .live
            .turns
            .get(&id)
            .is_some_and(|turn| turn.run_id.is_some())
        {
            notify("请先重新读取会话", "上一次运行的消息尚未保存到历史。");
            return;
        }
        self.dismiss_composer_menu(cx);
        self.live.model_errors.remove(&id);
        let paths = self.live.attachments.remove(&id).unwrap_or_default();
        self.live
            .turns
            .insert(id.clone(), Turn::new(&prompt, &paths));
        self.timeline.scroll.scroll_to_bottom();
        draft.update(cx, |draft, cx| {
            draft.set_value("", window, cx);
            draft.focus_handle(cx).focus(window, cx);
        });
        cx.notify();
        self.start_turn(id, prompt, paths, draft.clone(), window, cx);
    }

    fn start_turn(
        &mut self,
        id: String,
        prompt: String,
        paths: Vec<String>,
        draft: Entity<TextareaState>,
        window: &Window,
        cx: &mut Context<Self>,
    ) {
        let executor = cx.background_executor().clone();
        cx.spawn_in(window, async move |view, cx| {
            let payload =
                json!({"command":"send", "conversation_id":id, "prompt":prompt, "paths":paths});
            let started = executor
                .spawn(async move { request_payload::<SendReceipt>(payload) })
                .await;
            let receipt = match started {
                Ok(receipt) => receipt,
                Err(error) => {
                    let _ = view.update(cx, |this, cx| {
                        if let Some(turn) = this.live.turns.get_mut(&id) {
                            turn.running = false;
                            turn.stopping = false;
                        }
                        notify("无法发送消息", error.message());
                        cx.notify();
                    });
                    return;
                }
            };
            let cancel_queued = view
                .update(cx, |this, _| {
                    if let Some(turn) = this.live.turns.get_mut(&id) {
                        turn.bind_run(&receipt.run_id);
                        turn.started = true;
                        turn.stopping
                    } else {
                        false
                    }
                })
                .unwrap_or(false);
            if cancel_queued {
                let target = id.clone();
                let result = executor
                    .spawn(async move { request::<Value>("cancel", Some(&target)) })
                    .await;
                if let Err(error) = result {
                    let _ = view.update(cx, |this, cx| this.stop_failed(&id, error, cx));
                }
            }
            let mut failures = 0;
            let (error, model_error) = loop {
                let target = id.clone();
                let poll = executor
                    .spawn(async move { request::<Poll>("poll", Some(&target)) })
                    .await;
                match poll {
                    Ok(poll) => {
                        failures = 0;
                        if view
                            .update(cx, |this, cx| {
                                for event in poll.events {
                                    if event["type"] == "delegation" {
                                        if let Ok(task) =
                                            serde_json::from_value(event["task"].clone())
                                        {
                                            this.live
                                                .tasks
                                                .entry(id.clone())
                                                .or_default()
                                                .update(task);
                                        }
                                    } else if let Some(turn) = this.live.turns.get_mut(&id) {
                                        turn.event(event);
                                    }
                                }
                                cx.notify();
                            })
                            .is_err()
                        {
                            return;
                        }
                        if poll.done {
                            break (poll.error, poll.model_error);
                        }
                    }
                    Err(RequestError::Transport(error)) => {
                        failures += 1;
                        if failures == 1 {
                            let _ = view.update(cx, |_, _| notify("连接暂时中断", &error));
                        }
                        executor.timer(Duration::from_secs(1)).await;
                    }
                    Err(error) => break (Some(error.message().to_owned()), None),
                }
                executor.timer(Duration::from_millis(60)).await;
            };
            let target = id.clone();
            let snapshot = executor
                .spawn(async move { request::<Opened>("open", Some(&target)) })
                .await;
            let _ = cx.update(|window, cx| {
                let _ = view.update(cx, |this, cx| {
                    let send_after_stop = this
                        .live
                        .turns
                        .get(&id)
                        .is_some_and(|turn| turn.send_after_stop);
                    this.finish_turn(&id, snapshot, error, model_error, cx);
                    if send_after_stop
                        && this.chats.as_ref().and_then(|chats| chats.selected.as_deref())
                            == Some(id.as_str())
                    {
                        this.submit_draft(&draft, window, cx);
                    }
                });
            });
        })
        .detach();
    }

    fn finish_turn(
        &mut self,
        id: &str,
        snapshot: Result<Opened, RequestError>,
        error: Option<String>,
        model_error: Option<String>,
        cx: &mut Context<Self>,
    ) {
        if let Some(turn) = self.live.turns.get_mut(id) {
            turn.running = false;
            turn.stopping = false;
            turn.question = None;
            turn.model_failure = None;
        }
        if let Some(message) = model_error {
            self.live.model_errors.insert(id.to_owned(), message);
        }
        if let Some(error) = &error {
            notify("对话未完成", error);
        }
        match snapshot {
            Ok(chat) => {
                let saved = self
                    .live
                    .turns
                    .get(id)
                    .is_some_and(|turn| turn.saved_in(&chat.history));
                if saved {
                    self.live.turns.remove(id);
                }
                if let Some(chats) = &mut self.chats
                    && chats.selected.as_deref() == Some(id)
                {
                    let generation = chats.select(id.to_owned(), true);
                    let result = Ok(chat);
                    chats.complete_history(generation, id, &result);
                    chats.set_history(result);
                }
            }
            Err(error) => notify("无法读取最新聊天历史", error.message()),
        }
        self.reload_list(cx);
        self.load_inspector(cx);
        cx.notify();
    }

    pub(crate) fn retry_model(&mut self, id: String, token: String, cx: &mut Context<Self>) {
        let request = cx.background_executor().spawn(async move {
            request_payload::<bool>(
                json!({"command":"retry_model", "conversation_id":id, "token":token}),
            )
        });
        cx.spawn(async move |_, _| {
            if let Err(error) = request.await {
                notify("无法重试模型请求", error.message());
            }
        })
        .detach();
    }

    pub(crate) fn stop_turn(&mut self, cx: &mut Context<Self>) {
        let Some(id) = self.chats.as_ref().and_then(|chats| chats.selected.clone()) else {
            return;
        };
        let Some(turn) = self
            .live
            .turns
            .get_mut(&id)
            .filter(|turn| turn.running && !turn.stopping)
        else {
            return;
        };
        turn.stopping = true;
        cx.notify();
        if !turn.started {
            return;
        }
        let executor = cx.background_executor().clone();
        cx.spawn(async move |view, cx| {
            let target = id.clone();
            if let Err(error) = executor
                .spawn(async move { request::<Value>("cancel", Some(&target)) })
                .await
            {
                let _ = view.update(cx, |this, cx| this.stop_failed(&id, error, cx));
            }
        })
        .detach();
    }

    fn stop_failed(&mut self, id: &str, error: RequestError, cx: &mut Context<Self>) {
        if let Some(turn) = self.live.turns.get_mut(id) {
            turn.stopping = false;
            turn.send_after_stop = false;
        }
        notify("无法停止对话", error.message());
        cx.notify();
    }

    pub(crate) fn reply_to_question(
        &mut self,
        id: String,
        token: u64,
        selected: Vec<String>,
        text: Option<String>,
        cx: &mut Context<Self>,
    ) {
        let Some(turn) = self.live.turns.get_mut(&id) else {
            return;
        };
        let Some(question) = turn.question.as_mut().filter(|question| question.id == token) else {
            return;
        };
        if turn.answering {
            return;
        }
        let show_feedback = !selected.is_empty();
        if show_feedback {
            question.selected.clone_from(&selected);
        }
        turn.answering = true;
        cx.notify();
        let payload = json!({"command":"answer","conversation_id":id,"question_id":token,"selected":selected,"text":text});
        let executor = cx.background_executor().clone();
        cx.spawn(async move |view, cx| {
            if show_feedback {
                executor.timer(ANSWER_FEEDBACK).await;
                let still_pending = view
                    .update(cx, |this, _| {
                        this.live.turns.get(&id).is_some_and(|turn| {
                            turn.running
                                && turn.answering
                                && turn.question.as_ref().is_some_and(|question| question.id == token)
                        })
                    })
                    .unwrap_or(false);
                if !still_pending {
                    return;
                }
            }
            let result = executor
                .spawn(async move { request_payload::<bool>(payload) })
                .await;
            let _ = view.update(cx, |this, cx| {
                let Some(turn) = this.live.turns.get_mut(&id) else {
                    return;
                };
                if turn.question.as_ref().map(|question| question.id) != Some(token) {
                    return;
                }
                turn.answering = false;
                match result {
                    Ok(true) => turn.question = None,
                    Ok(false) => {
                        turn.question = None;
                        notify("此提问已结束", "");
                    }
                    Err(error) => notify("无法提交回答", error.message()),
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub(crate) fn choose_answer(
        &mut self,
        id: String,
        token: u64,
        label: String,
        cx: &mut Context<Self>,
    ) {
        let Some(question) = self
            .live
            .turns
            .get_mut(&id)
            .filter(|turn| !turn.answering)
            .and_then(|turn| turn.question.as_mut())
            .filter(|question| question.id == token)
        else {
            return;
        };
        if question.multi_select {
            if question.selected.contains(&label) {
                question.selected.retain(|item| item != &label);
            } else {
                question.selected.push(label);
            }
            cx.notify();
        } else {
            self.reply_to_question(id, token, vec![label], None, cx);
        }
    }
}
