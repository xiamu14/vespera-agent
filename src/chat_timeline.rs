use crate::{
    Vespera, chat_state::Chats, history_cards::text, inspector_collapse::CollapsibleSection,
    live_state::Turn, theme::*,
};
use gpui_kit::{base::Button, *};
use std::collections::HashMap;

#[derive(Default)]
pub struct ChatTimeline {
    pub conversation_id: Option<String>,
    pub scroll: ScrollHandle,
    pub anchors: HashMap<usize, f32>,
    pub active: Option<usize>,
}

fn active_at(anchors: &HashMap<usize, f32>, top: f32) -> Option<usize> {
    anchors
        .iter()
        .filter(|(_, y)| **y <= top)
        .max_by(|a, b| a.1.total_cmp(b.1))
        .map(|(index, _)| *index)
        .or(Some(0))
}

pub fn entries(chats: &Chats, turn: Option<&Turn>) -> Vec<(String, String)> {
    chats
        .history
        .visible()
        .into_iter()
        .flat_map(|chat| &chat.history)
        .flat_map(|message| &message.parts)
        .chain(turn.into_iter().flat_map(|turn| &turn.parts))
        .filter(|part| text(part, "part_kind") == "user-prompt")
        .filter_map(|part| {
            crate::history_cards::user_text(part)
                .map(|label| (crate::inspector::time_label(text(part, "timestamp")), label))
        })
        .collect()
}

pub fn marker(bubble: Div, index: usize, conversation_id: &str, cx: &Context<Vespera>) -> Div {
    let view = cx.entity().downgrade();
    let id = conversation_id.to_owned();
    column()
        .child(bubble)
        .on_children_prepainted(move |bounds, window, cx| {
            let Some(bounds) = bounds.first() else { return };
            let bounds = *bounds;
            let view = view.clone();
            let id = id.clone();
            window.defer(cx, move |_, cx| {
                let _ = view.update(cx, |this, cx| {
                    if this.timeline.conversation_id.as_deref() != Some(&id) {
                        return;
                    }
                    let scroll = &this.timeline.scroll;
                    let y = f32::from(bounds.top() - scroll.bounds().top() - scroll.offset().y);
                    this.timeline.anchors.insert(index, y);
                    let top = -f32::from(scroll.offset().y) + 40.;
                    let active = active_at(&this.timeline.anchors, top);
                    if this.timeline.active != active {
                        this.timeline.active = active;
                        cx.notify();
                    }
                });
            });
        })
}

#[cfg(test)]
mod tests {
    use super::{Chats, Turn, active_at, entries};
    use crate::{chat_data::Opened, chat_state::HistoryPhase};
    use serde_json::json;
    use std::collections::HashMap;

    #[test]
    fn timeline_includes_history_and_live_user_messages_in_order() {
        let chat: Opened = serde_json::from_value(json!({
            "conversation_id":"test", "history":[{"parts":[
                {"part_kind":"user-prompt","content":"first"},
                {"part_kind":"text","content":"reply"},
                {"part_kind":"user-prompt","content":["second", {"kind":"image"}]}
            ]}]
        }))
        .unwrap();
        let mut chats = Chats::default();
        chats.history = HistoryPhase::Showing(chat);
        let turn = Turn::new("third", &[]);
        assert_eq!(
            entries(&chats, Some(&turn))
                .into_iter()
                .map(|(_, text)| text)
                .collect::<Vec<_>>(),
            ["first", "second", "third"]
        );
        let anchors = HashMap::from([(0, 20.), (1, 400.), (2, 900.)]);
        assert_eq!(active_at(&anchors, 0.), Some(0));
        assert_eq!(active_at(&anchors, 500.), Some(1));
        assert_eq!(active_at(&anchors, 1000.), Some(2));
    }
}

pub fn render(app: &Vespera, cx: &Context<Vespera>) -> CollapsibleSection {
    let rows = app
        .chats
        .as_ref()
        .map(|chats| entries(chats, app.selected_turn()))
        .unwrap_or_default();
    CollapsibleSection::new(
        "timeline-toggle",
        "时间线",
        column()
            .gap(px(6.))
            .children(
                rows.into_iter()
                    .enumerate()
                    .map(|(index, (time, message))| {
                        let active = app.timeline.active == Some(index);
                        Button::new(SharedString::from(format!("timeline-{index}")))
                            .accessibility_label(message.clone())
                            .w_full()
                            .justify_start()
                            .gap(px(10.))
                            .px(px(8.))
                            .py(px(6.))
                            .rounded(limited_radius(CONTROL_RADIUS, 32.))
                            .bg(rgb(if active { FIELD } else { BACKGROUND }))
                            .child(timeline_time(time, if active { 1. } else { 0.65 }))
                            .child(crate::message_references::summary(&message))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                if let Some(y) = this.timeline.anchors.get(&index) {
                                    this.timeline
                                        .scroll
                                        .set_offset(point(px(0.), px((20. - *y).min(0.))));
                                    this.timeline.active = Some(index);
                                    cx.notify();
                                }
                            }))
                    }),
            ),
    )
}
