use crate::{Vespera, chat_data::request_payload, theme::*};
use gpui_kit::{
    base::{
        Button,
        input::{InlineToken, TextareaState},
    },
    *,
};
use serde::Deserialize;
use serde_json::json;
use std::ops::Range;

#[derive(Clone, Debug, PartialEq)]
struct Trigger {
    key: char,
    range: Range<usize>,
    query: String,
}

fn trigger(text: &str, cursor: usize) -> Option<Trigger> {
    let prefix = text.get(..cursor)?;
    let start = prefix
        .char_indices()
        .rev()
        .find(|(_, ch)| ch.is_whitespace())
        .map_or(0, |(index, ch)| index + ch.len_utf8());
    let mut chars = prefix[start..].chars();
    let key = chars.next()?;
    if !matches!(key, '/' | '@') {
        return None;
    }
    Some(Trigger {
        key,
        range: start..cursor,
        query: chars.as_str().to_owned(),
    })
}

#[derive(Clone, Deserialize)]
struct Choice {
    name: String,
    description: String,
}

#[derive(Clone, Deserialize)]
struct Reference {
    path: String,
    label: String,
    folder: bool,
    #[serde(default)]
    attachment: bool,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Choices {
    Slash {
        roles: Vec<Choice>,
        skills: Vec<Choice>,
    },
    Paths(Vec<Reference>),
}

#[derive(Default)]
pub struct ComposerMenu {
    target: Option<(EntityId, Trigger)>,
    choices: Option<Choices>,
    serial: u64,
    selected: usize,
    scroll: ScrollHandle,
}

impl Vespera {
    pub(crate) fn dismiss_composer_menu(&mut self, cx: &mut Context<Self>) {
        self.composer_menu = ComposerMenu {
            serial: self.composer_menu.serial + 1,
            ..Default::default()
        };
        cx.notify();
    }

    pub(crate) fn update_composer_menu(
        &mut self,
        draft: &Entity<TextareaState>,
        cx: &mut Context<Self>,
    ) {
        if self
            .selected_turn()
            .is_some_and(|turn| turn.composer_busy() || turn.stopping)
        {
            return;
        }
        let input = draft.read(cx);
        let Some(token) = draft_trigger(input) else {
            if self.composer_menu.target.is_some() {
                self.dismiss_composer_menu(cx);
            }
            return;
        };
        let Some(conversation_id) = self.chats.as_ref().and_then(|chat| chat.selected.clone())
        else {
            return;
        };
        let id = draft.entity_id();
        if self
            .composer_menu
            .target
            .as_ref()
            .is_some_and(|(previous, trigger)| {
                *previous == id && trigger.key == '/' && token.key == '/'
            })
        {
            self.composer_menu.target = Some((id, token));
            self.composer_menu.selected = 0;
            self.composer_menu.scroll.set_offset(Point::default());
            cx.notify();
            return;
        }
        let serial = self.composer_menu.serial + 1;
        self.composer_menu = ComposerMenu {
            target: Some((id, token.clone())),
            serial,
            ..Default::default()
        };
        let payload = json!({"command": "composer_choices", "conversation_id": conversation_id,
                             "query": (token.key == '@').then_some(token.query)});
        let executor = cx.background_executor().clone();
        cx.spawn(async move |view, cx| {
            if token.key == '@' {
                executor.timer(std::time::Duration::from_millis(100)).await;
                if !view
                    .update(cx, |this, _| this.composer_menu.serial == serial)
                    .unwrap_or(false)
                {
                    return;
                }
            }
            let result = executor
                .spawn(async move { request_payload::<Choices>(payload) })
                .await;
            let _ = view.update(cx, |this, cx| {
                if this.composer_menu.serial == serial {
                    match result {
                        Ok(choices) => this.composer_menu.choices = Some(choices),
                        Err(error) => {
                            this.dismiss_composer_menu(cx);
                            crate::system_notification::notify("无法读取引用", error.message());
                        }
                    }
                    cx.notify();
                }
            });
        })
        .detach();
        cx.notify();
    }
}

pub(crate) fn intercept_keys(
    draft: &Entity<TextareaState>,
    window: &Window,
    cx: &mut Context<Vespera>,
) -> Subscription {
    let app = cx.entity().downgrade();
    let draft = draft.clone();
    let window_id = window.window_handle().window_id();
    cx.intercept_keystrokes(move |event, window, cx| {
        let key = &event.keystroke.key;
        if window.window_handle().window_id() == window_id
            && matches!(key.as_str(), "up" | "down" | "escape")
            && event.keystroke.modifiers == Modifiers::none()
            && draft.read(cx).focus_handle(cx).is_focused(window)
            && app
                .update(cx, |app, cx| app.composer_menu_key(&draft, key, cx))
                .unwrap_or(false)
        {
            cx.stop_propagation();
        }
    })
}

fn draft_trigger(draft: &TextareaState) -> Option<Trigger> {
    let token = trigger(&draft.value(), draft.cursor())?;
    (!draft
        .tokens()
        .iter()
        .any(|span| span.range().contains(&token.range.start)))
    .then_some(token)
}

enum MenuRow<'a> {
    Heading(&'static str, bool),
    Choice {
        title: &'a str,
        description: &'a str,
        icon: &'static str,
        path: Option<&'a Reference>,
    },
}

impl ComposerMenu {
    fn target(&self, draft: &Entity<TextareaState>, cx: &App) -> Option<&Trigger> {
        let (id, token) = self.target.as_ref()?;
        (*id == draft.entity_id() && draft_trigger(draft.read(cx)).as_ref() == Some(token))
            .then_some(token)
    }

    fn rows(&self, token: &Trigger) -> Vec<MenuRow<'_>> {
        let mut rows = Vec::new();
        match &self.choices {
            Some(Choices::Slash { roles, skills }) => {
                let query = token.query.to_lowercase();
                for (heading, icon, choices) in
                    [("Agent", "chat-bot", roles), ("技能", "tool-case", skills)]
                {
                    let mut choices = choices
                        .iter()
                        .filter(|choice| matches_choice(choice, &query))
                        .peekable();
                    if choices.peek().is_some() {
                        rows.push(MenuRow::Heading(heading, !rows.is_empty()));
                        rows.extend(choices.map(|choice| MenuRow::Choice {
                            title: &choice.name,
                            description: &choice.description,
                            icon,
                            path: None,
                        }));
                    }
                }
            }
            Some(Choices::Paths(paths)) if !paths.is_empty() => {
                rows.push(MenuRow::Heading("历史附件和文件引用", false));
                rows.extend(paths.iter().map(|path| MenuRow::Choice {
                    title: &path.label,
                    description: "",
                    icon: if path.folder {
                        "folder-01"
                    } else {
                        "attachment-01"
                    },
                    path: Some(path),
                }));
            }
            _ => {}
        }
        rows
    }
}

pub(crate) fn menu(
    app: &Vespera,
    draft: &Entity<TextareaState>,
    cx: &Context<Vespera>,
) -> Option<impl IntoElement> {
    let menu = &app.composer_menu;
    let token = menu.target(draft, cx)?;
    let rows = menu.rows(token);
    const MAX_HEIGHT: Pixels = px(250.);
    let mut content = column()
        .id("composer-menu")
        .w_full()
        .max_h(MAX_HEIGHT - 2. * (CHAT_SECTION_PADDING_Y + px(1.)))
        .debug_selector(|| "composer-menu-viewport".into())
        .overflow_y_scroll()
        .track_scroll(&menu.scroll)
        .gap(px(4.));
    if rows.is_empty() {
        let message = match &menu.choices {
            None => "正在读取…",
            Some(Choices::Paths(_)) => "没有匹配的历史引用",
            Some(Choices::Slash { .. }) => "没有匹配的 Agent 或技能",
        };
        content = content.child(muted(message, 14.).flex_shrink_0());
    }
    let mut index = 0;
    for row in rows {
        match row {
            MenuRow::Heading(title, divider) => {
                content = content.child(
                    column()
                        .flex_shrink_0()
                        .gap(px(4.))
                        .children(divider.then(|| div().h(px(1.)).bg(rgb(BORDER))))
                        .child(
                            crate::theme::row()
                                .gap(px(8.))
                                .child(section_marker())
                                .child(label(title, 14.).text_color(rgb(INK))),
                        ),
                );
            }
            MenuRow::Choice {
                title,
                description,
                icon: glyph,
                path,
            } => {
                let selected = index == menu.selected;
                let draft = draft.clone();
                let token = token.clone();
                let title = title.to_owned();
                let path = path.cloned();
                content = content.child(
                    div().flex_shrink_0().child(
                        Button::new(SharedString::from(format!("composer-choice-{index}")))
                            .accessibility_label(title.clone())
                            .w_full()
                            .h(px(32.))
                            .justify_start()
                            .gap(px(8.))
                            .px(px(8.))
                            .rounded(limited_radius(CONTROL_RADIUS, 32.))
                            .bg(rgb(if selected { FIELD } else { BACKGROUND }))
                            .hover(|style| style.bg(rgb(FIELD)))
                            .child(icon(glyph, 14., if selected { PRIMARY } else { MUTED }))
                            .child(
                                label(title.clone(), 14.)
                                    .text_color(rgb(INK))
                                    .min_w_0()
                                    .truncate(),
                            )
                            .children((!description.is_empty()).then(|| {
                                muted(description.to_owned(), 14.)
                                    .min_w_0()
                                    .flex_1()
                                    .truncate()
                            }))
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.dismiss_composer_menu(cx);
                                insert_choice(
                                    &draft,
                                    &token,
                                    &title,
                                    glyph,
                                    path.as_ref(),
                                    window,
                                    cx,
                                );
                            })),
                    ),
                );
                index += 1;
            }
        }
    }
    Some(
        deferred(
            div()
                .absolute()
                .left_0()
                .right_0()
                .bottom_full()
                .pb(px(6.))
                .child(
                    card()
                        .debug_selector(|| "composer-choices".into())
                        .occlude()
                        .px(CHAT_SECTION_PADDING_X)
                        .py(CHAT_SECTION_PADDING_Y)
                        .shadow(vec![BoxShadow {
                            inset: false,
                            color: rgba(0x00000015).into(),
                            offset: point(px(0.), px(3.)),
                            blur_radius: px(14.),
                            spread_radius: px(0.),
                        }])
                        .child(content),
                ),
        )
        .with_priority(2),
    )
}

fn insert_choice(
    draft: &Entity<TextareaState>,
    token: &Trigger,
    title: &str,
    glyph: &str,
    path: Option<&Reference>,
    window: &mut Window,
    cx: &mut App,
) {
    draft.update(cx, |draft, cx| {
        if draft_trigger(draft).as_ref() != Some(token) {
            return;
        }
        let badge = match path {
            Some(path) if path.attachment => {
                crate::text_fields::attachment_reference(&path.path, &path.label)
            }
            Some(path) => crate::text_fields::path_reference(&path.path, path.folder),
            None => InlineToken::new(format!("command:{glyph}:{title}"), format!("/{title}"))
                .with_label(title.to_owned()),
        };
        if draft
            .replace_range_with_token(token.range.clone(), badge, window, cx)
            .is_ok()
        {
            draft.replace(" ", window, cx);
        }
        draft.focus(window, cx);
    });
}

fn matches_choice(choice: &Choice, query: &str) -> bool {
    choice.name.to_lowercase().contains(query) || choice.description.to_lowercase().contains(query)
}

impl Vespera {
    pub(crate) fn choose_composer_menu(
        &mut self,
        draft: &Entity<TextareaState>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> bool {
        let menu = &self.composer_menu;
        let Some(token) = menu.target(draft, cx).cloned() else {
            return false;
        };
        if menu.choices.is_none() {
            return true;
        }
        if let Some(MenuRow::Choice {
            title, icon, path, ..
        }) = menu
            .rows(&token)
            .into_iter()
            .filter(|row| matches!(row, MenuRow::Choice { .. }))
            .nth(menu.selected)
        {
            insert_choice(draft, &token, title, icon, path, window, cx);
        }
        self.dismiss_composer_menu(cx);
        true
    }

    pub(crate) fn composer_menu_key(
        &mut self,
        draft: &Entity<TextareaState>,
        key: &str,
        cx: &mut Context<Self>,
    ) -> bool {
        let menu = &mut self.composer_menu;
        let Some(token) = menu.target(draft, cx) else {
            return false;
        };
        if key == "escape" {
            self.dismiss_composer_menu(cx);
            return true;
        }
        let items: Vec<_> = menu
            .rows(token)
            .iter()
            .enumerate()
            .filter_map(|(index, row)| matches!(row, MenuRow::Choice { .. }).then_some(index))
            .collect();
        if items.is_empty() {
            return false;
        }
        menu.selected = if key == "up" {
            menu.selected.saturating_sub(1)
        } else {
            (menu.selected + 1).min(items.len() - 1)
        };
        menu.scroll.scroll_to_item(items[menu.selected]);
        cx.notify();
        true
    }
}

#[cfg(test)]
#[path = "composer_menu_tests.rs"]
mod tests;
