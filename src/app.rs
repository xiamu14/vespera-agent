use crate::masked_scroll::MaskedScroll;
use crate::{artifacts, chat, fixtures, memory, sidebar, status, theme::*};
use gpui_kit::component::TitleBar;
use gpui_kit::*;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum InspectorTab {
    #[default]
    Status,
    Artifacts,
    Memory,
}

impl InspectorTab {
    pub const ALL: [Self; 3] = [Self::Status, Self::Artifacts, Self::Memory];

    fn title(self) -> &'static str {
        match self {
            Self::Status => "状态",
            Self::Artifacts => "产物",
            Self::Memory => "记忆",
        }
    }

    fn icon(self) -> &'static str {
        match self {
            Self::Status => "task-01",
            Self::Artifacts => "package",
            Self::Memory => "brain-02",
        }
    }
}

#[derive(Default)]
pub struct Vespera {
    pub tab: InspectorTab,
    pub(crate) timeline: crate::chat_timeline::ChatTimeline,
    pub(crate) chats: Option<crate::chat_state::Chats>,
    search_observed: bool,
    observed_draft: Option<EntityId>,
    drafts: std::collections::HashMap<String, Entity<gpui_kit::base::input::TextareaState>>,
    draft_subscriptions: Vec<Subscription>,
    pub(crate) live: crate::live_chat::LiveChat,
    pub(crate) inspection: crate::inspector::InspectionState,
    pub(crate) reset_search: bool,
    pub(crate) no_search_matches: bool,
    pub(crate) search_query: String,
}

impl Vespera {
    pub(crate) fn reference_path(
        &mut self,
        conversation_id: &str,
        path: &std::path::Path,
        folder: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self
            .chats
            .as_ref()
            .and_then(|chats| chats.selected.as_deref())
            != Some(conversation_id)
        {
            return;
        }
        let Some(path) = path.to_str() else {
            crate::system_notification::notify("无法添加引用", "路径包含无法保存的字符。");
            return;
        };
        let Some(draft) = self.drafts.get(conversation_id) else {
            return;
        };
        let token = crate::text_fields::path_reference(path, folder);
        draft.update(cx, |draft, cx| {
            if draft.replace_with_token(token, window, cx).is_err() {
                crate::system_notification::notify("无法添加引用", "文件名包含不支持的字符。");
                return;
            }
            draft.focus_handle(cx).focus(window, cx);
        });
        cx.notify();
    }
}

impl Vespera {
    pub fn preview(tab: InspectorTab) -> Self {
        Self {
            tab,
            ..Self::default()
        }
    }

    pub(crate) fn new(cx: &mut Context<Self>) -> Self {
        let mut this = Self::default();
        if crate::agent_bridge::AgentBridge::is_bundled() {
            this.chats = Some(Default::default());
            this.reload_list(cx);
        }
        this
    }

    fn tab_bar(&self, cx: &mut Context<Self>) -> Div {
        const PADDING: f32 = 3.;
        const BORDER_WIDTH: f32 = 1.;
        let control_height = INSPECTOR_TAB_HEIGHT - 2. * (PADDING + BORDER_WIDTH);
        let controls = InspectorTab::ALL.map(|tab| {
            let active = tab == self.tab;
            let mut control = row()
                .id(tab.title())
                .flex_1()
                .min_w_0()
                .h(px(control_height))
                .gap(px(8.))
                .justify_center()
                .rounded(limited_radius(
                    CONTROL_RADIUS - PADDING - BORDER_WIDTH,
                    control_height,
                ))
                .text_color(rgb(if active { INK } else { MUTED }))
                .child(icon(tab.icon(), 14., if active { INK } else { MUTED }))
                .child(label(tab.title(), 16.))
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.tab = tab;
                    cx.notify();
                }));
            if active {
                control = control.bg(rgb(0xffffff)).shadow(vec![BoxShadow {
                    inset: false,
                    color: rgba(0x00000010).into(),
                    offset: point(px(0.), px(2.)),
                    blur_radius: px(4.),
                    spread_radius: px(0.),
                }]);
            }
            control
        });
        row()
            .h(px(INSPECTOR_TAB_HEIGHT))
            .w_full()
            .flex_shrink_0()
            .p(px(PADDING))
            .gap(px(8.))
            .rounded(limited_radius(CONTROL_RADIUS, INSPECTOR_TAB_HEIGHT))
            .border(px(BORDER_WIDTH))
            .border_color(rgb(0xededee))
            .bg(rgb(0xf8f8f8))
            .children(controls)
    }

    fn inspector(&self, cx: &mut Context<Self>) -> Div {
        let selected = self
            .chats
            .as_ref()
            .and_then(|chats| chats.selected.as_deref());
        let data = self.inspection.data(selected);
        let content = match (self.tab, self.chats.is_some()) {
            (InspectorTab::Status, false) => {
                status::status(&fixtures::TIMELINE[..2]).into_any_element()
            }
            (InspectorTab::Status, true) => status::live(data, self, cx).into_any_element(),
            (InspectorTab::Artifacts, live) => MaskedScroll::new(
                "inspector-content",
                64.,
                if live {
                    artifacts::live(data)
                } else {
                    artifacts::artifacts()
                },
            )
            .into_any_element(),
            (InspectorTab::Memory, live) => MaskedScroll::new(
                "inspector-content",
                64.,
                if live {
                    memory::live(data)
                } else {
                    memory::memory()
                },
            )
            .into_any_element(),
        };
        column()
            .flex_1()
            .min_w_0()
            .h_full()
            .pt(px(PANEL_TOP))
            .pl(px(30.))
            .pr(px(20.))
            .pb(px(PANEL_TOP))
            .gap(px(20.))
            .child(self.tab_bar(cx))
            .child(content)
    }
}

impl Render for Vespera {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let selected = self.chats.as_ref().and_then(|chats| chats.selected.clone());
        if self.timeline.conversation_id != selected {
            self.timeline = crate::chat_timeline::ChatTimeline {
                conversation_id: selected,
                ..Default::default()
            };
        }
        let search = crate::text_fields::search(window, cx);
        if self.chats.is_some() && !self.search_observed {
            self.search_observed = true;
            cx.observe(&search, |this, search, cx| {
                this.search_query = search.read(cx).value().trim().to_lowercase();
                this.update_search_status();
                cx.notify();
            })
            .detach();
        }
        if std::mem::take(&mut self.reset_search) {
            self.search_query.clear();
            self.no_search_matches = false;
            search.update(cx, |search, cx| search.set_value("", window, cx));
        }
        let conversation_id = self
            .chats
            .as_ref()
            .and_then(|chats| chats.selected.as_deref());
        let draft_key = conversation_id.unwrap_or_default().to_owned();
        let draft = self
            .drafts
            .entry(draft_key)
            .or_insert_with(|| {
                crate::text_fields::draft(window, cx, self.chats.is_none(), conversation_id)
            })
            .clone();
        if self.observed_draft != Some(draft.entity_id()) {
            self.observed_draft = Some(draft.entity_id());
            self.draft_subscriptions.clear();
            self.draft_subscriptions
                .push(cx.observe(&draft, |_, _, cx| cx.notify()));
            if self.chats.is_some() {
                self.draft_subscriptions.push(cx.subscribe_in(
                    &draft,
                    window,
                    |this, draft, event, window, cx| {
                        if let gpui_kit::base::input::InputEvent::PressEnter {
                            shift: false, ..
                        } = event
                        {
                            this.submit_draft(&draft, window, cx);
                        }
                    },
                ));
            }
        }
        let work_path = self
            .chats
            .as_ref()
            .and_then(|chats| chats.history.visible())
            .and_then(|chat| chat.work_path.clone());
        let breadcrumb = if self.chats.is_none() {
            Some("advanced-course  /  coder-cli".to_owned())
        } else {
            work_path
        };
        let (sidebar_width, chat_width) = column_widths(
            window.viewport_size().width.into(),
            window.is_fullscreen() || window.is_maximized(),
        );
        column()
            .size_full()
            .overflow_hidden()
            .bg(rgb(BACKGROUND))
            .text_color(rgb(INK))
            .font(body_font())
            .child(
                TitleBar::new()
                    .h(px(50.))
                    .pl(px(0.))
                    .pr(px(20.))
                    .bg(rgb(BACKGROUND))
                    .border_color(rgb(0xededee))
                    .child(
                        row()
                            .ml(px(sidebar_width))
                            .pl(px(20.))
                            .gap(px(12.))
                            .children(breadcrumb.map(|path| {
                                row()
                                    .gap(px(12.))
                                    .child(icon("folder-01", 18., MUTED))
                                    .child(muted(path, 14.).whitespace_nowrap().overflow_hidden())
                            })),
                    )
                    .child(
                        row()
                            .gap(px(20.))
                            .child(icon("notification-01", 18., MUTED))
                            .child(icon("settings-01", 18., MUTED)),
                    ),
            )
            .child(
                row()
                    .flex_1()
                    .min_h_0()
                    .w_full()
                    .items_start()
                    .child(sidebar::sidebar(
                        sidebar_width,
                        &search,
                        self.chats.as_ref(),
                        self.inspection.daily_usage(),
                        cx,
                    ))
                    .child(chat::chat(chat_width, &draft, self, cx))
                    .child(self.inspector(cx)),
            )
    }
}
