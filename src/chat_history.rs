use crate::{
    Vespera,
    chat::{assistant_message, user_message},
    chat_state::{Chats, HistoryPhase},
    history_cards,
    live_state::{LiveChat, Turn},
    theme::*,
};
use gpui_kit::*;
use std::collections::HashMap;

const CHAT_INSET: f32 = 30.;

pub fn live_history(chats: &Chats, live: &LiveChat, width: f32, cx: &Context<Vespera>) -> Div {
    let mut content = column().gap(px(20.)).pt(px(20.)).px(px(CHAT_INSET));
    let Some(chat) = chats.history.visible() else {
        return content;
    };
    let turn = live
        .turns
        .get(&chat.conversation_id)
        .filter(|turn| turn.running || !turn.saved_in(&chat.history));
    if matches!(chats.history, HistoryPhase::RefreshFailed(_)) {
        let id = chat.conversation_id.clone();
        content = content.child(crate::sidebar::reload_row(
            "reload-history",
            "历史刷新失败",
            cx.listener(move |this, _, _, cx| this.open_chat(id.clone(), true, cx)),
        ));
    }
    let parts = history_parts(&chat.history, turn);
    let results: HashMap<_, _> = parts
        .iter()
        .copied()
        .filter(|part| {
            matches!(
                history_cards::text(part, "part_kind"),
                "tool-return" | "builtin-tool-return" | "retry-prompt"
            )
        })
        .map(|part| (history_cards::text(part, "tool_call_id"), part))
        .collect();
    let mut tools_shown = std::collections::HashSet::new();
    let mut header_shown = std::collections::HashSet::new();
    let mut section = 0;
    for (index, part) in parts.iter().enumerate() {
        let part = *part;
        if answered_question(
            part,
            results
                .get(history_cards::text(part, "tool_call_id"))
                .copied(),
        ) {
            continue;
        }
        let key = format!("history-{}-{index}", chat.conversation_id);
        let saved_seconds = chat
            .history
            .iter()
            .find(|message| message.parts.iter().any(|entry| std::ptr::eq(entry, part)))
            .and_then(|message| message.run_id.as_deref())
            .and_then(|run| chat.run_seconds.get(run))
            .copied()
            .unwrap_or_default();
        let live =
            turn.is_some_and(|turn| turn.parts.iter().any(|entry| std::ptr::eq(entry, part)));
        match history_cards::text(part, "part_kind") {
            "user-prompt" => {
                section += 1;
                if let Some(text) = history_cards::user_text(part) {
                    content = content.child(crate::chat_timeline::marker(
                        user_message(key.clone(), text, width),
                        section - 1,
                        &chat.conversation_id,
                        cx,
                    ));
                }
            }
            "text" | "tool-call" | "builtin-tool-call" => {
                if !visible_reply(
                    part,
                    results
                        .get(history_cards::text(part, "tool_call_id"))
                        .copied(),
                ) {
                    continue;
                }
                if header_shown.insert(section) {
                    content = content.child(crate::response_time::ResponseTime {
                        seconds: if live {
                            turn.map(Turn::elapsed_seconds).unwrap_or_default()
                        } else {
                            saved_seconds
                        },
                        running: live && turn.is_some_and(Turn::timer_running),
                    });
                }
                if history_cards::text(part, "part_kind") == "text" {
                    if !history_cards::text(part, "content").is_empty() {
                        content = content.child(assistant_message(
                            SharedString::from(key),
                            history_cards::text(part, "content").to_owned(),
                            width,
                        ));
                    }
                } else if tools_shown.insert(section) {
                    let entries = parts
                        .iter()
                        .skip(index)
                        .take_while(|entry| {
                            history_cards::text(entry, "part_kind") != "user-prompt"
                        })
                        .filter(|entry| {
                            matches!(
                                history_cards::text(entry, "part_kind"),
                                "tool-call" | "builtin-tool-call"
                            )
                        })
                        .filter(|call| {
                            !answered_question(
                                call,
                                results
                                    .get(history_cards::text(call, "tool_call_id"))
                                    .copied(),
                            )
                        })
                        .map(|call| {
                            let id = history_cards::text(call, "tool_call_id");
                            crate::tool_section::ToolEntry {
                                call: (*call).clone(),
                                result: results.get(id).map(|result| (*result).clone()),
                                pending: live && turn.is_some_and(|turn| turn.running),
                                approval: chat.approvals.get(id).cloned(),
                            }
                        })
                        .collect();
                    content = content.child(crate::tool_section::Tools {
                        id: key.into(),
                        entries,
                        workspace: chat.workspace.clone().or(chat.work_path.clone()),
                        width: (width - 2. * CHAT_INSET - 36.).max(0.),
                    });
                }
            }
            _ => {}
        }
    }
    if let Some(turn) = turn
        && let Some(question) = &turn.question
    {
        content = content.child(history_cards::question(
            question,
            &chat.conversation_id,
            turn.answering,
            px(width - 2. * CHAT_INSET),
            cx,
        ));
    }
    if let Some(failure) = turn.and_then(|turn| turn.model_failure.as_ref()) {
        let id = chat.conversation_id.clone();
        let token = failure.token.clone();
        content = content.child(
            chat_card()
                .px(CHAT_SECTION_PADDING_X)
                .py(CHAT_SECTION_PADDING_Y)
                .gap(px(8.))
                .child(label(failure.message.clone(), 14.))
                .child(
                    row().w_full().justify_end().child(
                        gpui_kit::base::Button::new("retry-model-request")
                            .accessibility_label("重试模型请求")
                            .disabled(turn.is_some_and(|turn| turn.stopping))
                            .px(px(10.))
                            .py(px(6.))
                            .rounded(px(DETAIL_RADIUS))
                            .border_1()
                            .border_color(rgb(BORDER))
                            .bg(rgb(0xffffff))
                            .child(
                                label("重试", 14.)
                                    .font_weight(FontWeight::NORMAL)
                                    .text_color(rgb(PRIMARY)),
                            )
                            .on_click(cx.listener(move |app, _, _, cx| {
                                app.retry_model(id.clone(), token.clone(), cx)
                            })),
                    ),
                ),
        );
    } else if let Some(message) = live.model_errors.get(&chat.conversation_id) {
        content = content.child(
            chat_card()
                .px(CHAT_SECTION_PADDING_X)
                .py(CHAT_SECTION_PADDING_Y)
                .child(label(message.clone(), 14.)),
        );
    } else if turn.is_some_and(|turn| waiting_for_reply(turn, &results)) {
        content = content.child(crate::thinking::Thinking {
            id: format!("thinking-{}", chat.conversation_id).into(),
        });
    }
    content
}

fn history_parts<'a>(
    history: &'a [crate::chat_data::Message],
    turn: Option<&'a Turn>,
) -> Vec<&'a serde_json::Value> {
    history
        .iter()
        .filter(|message| !turn.is_some_and(|turn| turn.owns_message(message)))
        .flat_map(|message| &message.parts)
        .chain(turn.into_iter().flat_map(|turn| &turn.parts))
        .filter(|part| {
            history_cards::text(part, "part_kind") != "user-prompt"
                || history_cards::user_text(part).is_some()
        })
        .collect()
}

fn visible_reply(part: &serde_json::Value, result: Option<&serde_json::Value>) -> bool {
    match history_cards::text(part, "part_kind") {
        "text" => !history_cards::text(part, "content").trim().is_empty(),
        "tool-call" | "builtin-tool-call" => !answered_question(part, result),
        _ => false,
    }
}

fn waiting_for_reply(turn: &Turn, results: &HashMap<&str, &serde_json::Value>) -> bool {
    turn.running
        && turn.question.is_none()
        && !turn
            .parts
            .iter()
            .rev()
            .take_while(|part| history_cards::text(part, "part_kind") != "user-prompt")
            .any(|part| {
                visible_reply(
                    part,
                    results
                        .get(history_cards::text(part, "tool_call_id"))
                        .copied(),
                )
            })
}

fn answered_question(call: &serde_json::Value, result: Option<&serde_json::Value>) -> bool {
    history_cards::text(call, "tool_name") == "ask_user_question"
        && result.is_some_and(|result| result["outcome"] == "success")
}

#[cfg(test)]
mod tests {
    use super::{answered_question, history_parts, visible_reply, waiting_for_reply};
    use crate::chat_data::Message;
    use crate::live_state::Turn;
    use serde_json::json;

    #[test]
    fn saved_and_optimistic_prompts_render_once_without_text_deduplication() {
        let mut turn = Turn::new("继续", &[]);
        turn.event(json!({"type":"run_started", "run_id":"current"}));
        let history = vec![Message {
            run_id: Some("current".into()),
            parts: vec![json!({"part_kind":"user-prompt", "content":"继续"})],
        }];
        assert_eq!(history_parts(&history, Some(&turn)).len(), 1);
        let mut history = history;
        history.insert(
            0,
            Message {
                run_id: Some("earlier".into()),
                parts: vec![json!({"part_kind":"user-prompt", "content":"继续"})],
            },
        );
        assert_eq!(history_parts(&history, Some(&turn)).len(), 2);
        assert_eq!(history_parts(&history, None).len(), 2);
    }
    use std::collections::HashMap;

    #[test]
    fn only_successfully_answered_question_tools_are_hidden() {
        let call = json!({"tool_name": "ask_user_question"});
        let success = json!({"outcome": "success"});
        assert!(answered_question(&call, Some(&success)));
        assert!(!answered_question(&call, None));
        assert!(!answered_question(&call, Some(&json!({"outcome":"error"}))));
        assert!(!answered_question(
            &json!({"tool_name":"read_file"}),
            Some(&success)
        ));
    }

    #[test]
    fn empty_history_sections_have_no_reply_header() {
        for part in [
            json!({"part_kind":"user-prompt", "content":"/executor 所有修改写 commit"}),
            json!({"part_kind":"text", "content":" \n "}),
            json!({"part_kind":"thinking", "content":"internal"}),
            json!({"part_kind":"instruction-baseline"}),
        ] {
            assert!(!visible_reply(&part, None));
        }
        let call = json!({"part_kind":"tool-call", "tool_name":"ask_user_question"});
        assert!(!visible_reply(&call, Some(&json!({"outcome":"success"}))));
        assert!(visible_reply(&call, None));
        assert!(visible_reply(
            &json!({"part_kind":"text", "content":"已完成"}),
            None
        ));
    }

    #[test]
    fn model_start_and_empty_stream_parts_keep_the_waiting_indicator() {
        let mut turn = Turn::new("/executor commit", &[]);
        let results = HashMap::new();
        turn.event(json!({"type":"agent", "event":{"event_kind":"model_request_started"}}));
        turn.event(
            json!({"type":"agent", "event":{"event_kind":"part_start", "index":0,
            "part":{"part_kind":"text", "content":""}}}),
        );
        assert!(waiting_for_reply(&turn, &results));
        turn.event(
            json!({"type":"agent", "event":{"event_kind":"part_delta", "index":0,
            "delta":{"part_delta_kind":"text", "content_delta":"已完成"}}}),
        );
        assert!(!waiting_for_reply(&turn, &results));
        turn.parts.truncate(1);
        turn.running = false;
        assert!(!waiting_for_reply(&turn, &results));
    }
}
