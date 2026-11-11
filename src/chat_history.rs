use crate::{
    Vespera,
    chat::{assistant_message, user_message},
    chat_state::{Chats, HistoryPhase},
    history_cards,
    live_state::Turn,
    theme::*,
};
use gpui_kit::*;
use std::collections::HashMap;

const CHAT_INSET: f32 = 30.;

pub fn live_history(chats: &Chats, turn: Option<&Turn>, width: f32, cx: &Context<Vespera>) -> Div {
    let mut content = column().gap(px(20.)).pt(px(20.)).px(px(CHAT_INSET));
    let Some(chat) = chats.history.visible() else {
        return content;
    };
    if matches!(chats.history, HistoryPhase::RefreshFailed(_)) {
        let id = chat.conversation_id.clone();
        content = content.child(crate::sidebar::reload_row(
            "reload-history",
            "历史刷新失败",
            cx.listener(move |this, _, _, cx| this.open_chat(id.clone(), true, cx)),
        ));
    }
    let parts: Vec<_> = chat
        .history
        .iter()
        .flat_map(|message| &message.parts)
        .chain(turn.into_iter().flat_map(|turn| &turn.parts))
        .filter(|part| {
            history_cards::text(part, "part_kind") != "user-prompt"
                || history_cards::user_text(part).is_some()
        })
        .collect();
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
                if !live || turn.is_some_and(|turn| turn.llm_started.is_some()) {
                    let seconds = if live {
                        turn.and_then(|turn| turn.llm_started)
                            .map(|start| start.elapsed().as_secs())
                            .unwrap_or_default()
                    } else {
                        saved_seconds
                    };
                    content = content.child(crate::response_time::ResponseTime {
                        seconds,
                        running: live && turn.is_some_and(|turn| turn.running),
                    });
                    header_shown.insert(section);
                }
            }
            "text" | "tool-call" | "builtin-tool-call" => {
                if history_cards::text(part, "part_kind") == "text"
                    && history_cards::text(part, "content").trim().is_empty()
                {
                    continue;
                }
                if header_shown.insert(section) {
                    content = content.child(crate::response_time::ResponseTime {
                        seconds: turn
                            .and_then(|turn| turn.llm_started)
                            .map(|start| start.elapsed().as_secs())
                            .unwrap_or_default(),
                        running: live && turn.is_some_and(|turn| turn.running),
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
        if history_cards::text(part, "tool_name") == "ask_user_question"
            && history_cards::text(part, "part_kind") == "tool-call"
            && let Some(result) = results.get(history_cards::text(part, "tool_call_id"))
        {
            let args = part["args"]
                .as_str()
                .and_then(|value| serde_json::from_str::<serde_json::Value>(value).ok());
            let args = args.as_ref().unwrap_or(&part["args"]);
            if let Some(questions) = args["questions"].as_array() {
                content = content.children(questions.iter().map(|question| {
                    history_cards::choice(
                        question,
                        &result["content"],
                        if result["outcome"] == "success" {
                            "已完成"
                        } else {
                            "已中断"
                        },
                    )
                }));
            }
        }
    }
    if let Some(turn) = turn
        && let Some(question) = &turn.question
    {
        content = content.child(history_cards::question(
            question,
            &chat.conversation_id,
            turn.answering,
            cx,
        ));
    }
    if turn
        .is_some_and(|turn| turn.running && turn.question.is_none() && turn.llm_started.is_none())
    {
        content = content.child(crate::thinking::Thinking {
            id: format!("thinking-{}", chat.conversation_id).into(),
        });
    }
    content
}
