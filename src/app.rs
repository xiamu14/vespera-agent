use crate::masked_scroll::MaskedScroll;
use crate::{artifacts, chat, fixtures, memory, sidebar, status, theme::*};
use gpui_kit::*;
use gpui_kit::{base::animation::ease_in_out_cubic, component::TitleBar};
use std::time::Instant;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum InspectorTab {
    #[default]
    Status,
    Artifacts,
    Memory,
}

impl InspectorTab {
    pub const ALL: [Self; 3] = [Self::Status, Self::Artifacts, Self::Memory];

    fn position(self) -> f32 {
        self as u8 as f32
    }

    fn title(self) -> &'static str {
        match self {
            Self::Status => "状态",
            Self::Artifacts => "交付",
            Self::Memory => "记忆",
        }
    }

    fn icon(self) -> &'static str {
        match self {
            Self::Status => "computer-activity",
            Self::Artifacts => "package",
            Self::Memory => "brain-02",
        }
    }
}

#[derive(Default)]
pub struct Vespera {
    pub tab: InspectorTab,
    tab_slide: Option<(f32, Instant)>,
    pub(crate) composer_menu: crate::composer_menu::ComposerMenu,
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

    fn tab_position(&self, now: Instant) -> f32 {
        let target = self.tab.position();
        self.tab_slide.map_or(target, |(from, started)| {
            let time = (now.duration_since(started).as_secs_f32() / 0.2).min(1.);
            from + (target - from) * ease_in_out_cubic(time)
        })
    }

    fn tab_bar(&self, window: &mut Window, cx: &mut Context<Self>) -> Div {
        const PADDING: f32 = 3.;
        const BORDER_WIDTH: f32 = 1.;
        const GAP: f32 = 8.;
        let inset = PADDING + BORDER_WIDTH;
        let control_height = INSPECTOR_TAB_HEIGHT - 2. * inset;
        let radius = limited_radius(CONTROL_RADIUS - inset, control_height);
        let now = Instant::now();
        let position = if cx.reduce_motion() {
            self.tab.position()
        } else {
            if self
                .tab_slide
                .is_some_and(|(_, started)| now.duration_since(started).as_secs_f32() < 0.2)
            {
                window.request_animation_frame();
            }
            self.tab_position(now)
        };
        let controls = InspectorTab::ALL.map(|tab| {
            let color = if tab == self.tab { INK } else { MUTED };
            row()
                .id(tab.title())
                .flex_1()
                .min_w_0()
                .h(px(control_height))
                .gap(px(8.))
                .justify_center()
                .text_color(rgb(color))
                .child(icon(tab.icon(), 14., color))
                .child(label(tab.title(), 16.))
                .on_click(cx.listener(move |this, _, _, cx| {
                    if this.tab != tab {
                        let now = Instant::now();
                        this.tab_slide =
                            (!cx.reduce_motion()).then(|| (this.tab_position(now), now));
                        this.tab = tab;
                        cx.notify();
                    }
                }))
        });
        row()
            .h(px(INSPECTOR_TAB_HEIGHT))
            .w_full()
            .flex_shrink_0()
            .p(px(PADDING))
            .rounded(limited_radius(CONTROL_RADIUS, INSPECTOR_TAB_HEIGHT))
            .border(px(BORDER_WIDTH))
            .border_color(rgb(0xededee))
            .bg(rgb(0xf8f8f8))
            .child(
                row()
                    .relative()
                    .w_full()
                    .h(px(control_height))
                    .gap(px(GAP))
                    .child(
                        canvas(
                            |_, _, _| (),
                            move |bounds, (), window, _| {
                                let width = ((bounds.size.width - px(2. * GAP)) / 3.).max(px(0.));
                                let plate = Bounds {
                                    origin: bounds.origin
                                        + point((width + px(GAP)) * position, px(0.)),
                                    size: size(width, px(control_height)),
                                };
                                let corners = Corners::all(radius);
                                window.paint_drop_shadows(
                                    plate,
                                    corners,
                                    &[BoxShadow {
                                        inset: false,
                                        color: rgba(0x00000010).into(),
                                        offset: point(px(0.), px(2.)),
                                        blur_radius: px(4.),
                                        spread_radius: px(0.),
                                    }],
                                );
                                let mut background = fill(plate, rgb(0xffffff));
                                background.corner_radii = corners;
                                window.paint_quad(background);
                            },
                        )
                        .absolute()
                        .inset_0(),
                    )
                    .children(controls),
            )
    }

    fn inspector(&self, window: &mut Window, cx: &mut Context<Self>) -> Div {
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
                    artifacts::live(data, selected.is_some())
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
            .child(self.tab_bar(window, cx))
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
                self.draft_subscriptions
                    .push(crate::composer_menu::intercept_keys(&draft, window, cx));
                self.draft_subscriptions.push(cx.subscribe_in(
                    &draft,
                    window,
                    |this, draft, event, window, cx| {
                        if matches!(event, gpui_kit::base::input::InputEvent::Change) {
                            this.update_composer_menu(&draft, cx);
                        }
                        if let gpui_kit::base::input::InputEvent::PressEnter {
                            shift: false, ..
                        } = event
                        {
                            if !this.choose_composer_menu(&draft, window, cx) {
                                this.submit_draft(&draft, window, cx);
                            }
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
            Some("advanced-course  /  ani".to_owned())
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
                    .child(self.inspector(window, cx)),
            )
    }
}

#[cfg(test)]
mod tests {
    use super::{InspectorTab, Vespera};
    use std::time::{Duration, Instant};

    #[test]
    fn tab_plate_retargets_from_its_current_position() {
        let start = Instant::now();
        let mut app = Vespera {
            tab: InspectorTab::Memory,
            tab_slide: Some((0., start)),
            ..Vespera::default()
        };
        let now = start + Duration::from_millis(50);
        let position = app.tab_position(now);
        assert!(position > 0. && position < 1.);
        app.tab_slide = Some((position, now));
        app.tab = InspectorTab::Status;
        assert_eq!(app.tab_position(now), position);
        assert_eq!(app.tab_position(now + Duration::from_millis(200)), 0.);
    }
}
