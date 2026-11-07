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
}

impl Vespera {
    fn tab_bar(&self, cx: &mut Context<Self>) -> Div {
        const RADIUS: f32 = 16.;
        const PADDING: f32 = 5.;
        const BORDER_WIDTH: f32 = 1.;
        let controls = InspectorTab::ALL.map(|tab| {
            let active = tab == self.tab;
            let mut control = row()
                .id(tab.title())
                .flex_1()
                .min_w_0()
                .h(px(40.))
                .gap(px(8.))
                .justify_center()
                .rounded(px(RADIUS - PADDING - BORDER_WIDTH))
                .text_color(rgb(if active { INK } else { MUTED }))
                .child(icon(tab.icon(), 18., if active { INK } else { MUTED }))
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
            .rounded(px(RADIUS))
            .border(px(BORDER_WIDTH))
            .border_color(rgb(0xededee))
            .bg(rgb(0xf8f8f8))
            .children(controls)
    }

    fn inspector(&self, cx: &mut Context<Self>) -> Div {
        let content = match self.tab {
            // The second timeline entry is the latest message in this mock.
            InspectorTab::Status => status::status(&fixtures::TIMELINE[..2]).into_any_element(),
            InspectorTab::Artifacts => {
                MaskedScroll::new("inspector-content", 64., artifacts::artifacts())
                    .into_any_element()
            }
            InspectorTab::Memory => {
                MaskedScroll::new("inspector-content", 64., memory::memory()).into_any_element()
            }
        };
        column()
            .flex_1()
            .min_w_0()
            .h_full()
            .pt(px(PANEL_TOP))
            .pl(px(30.))
            .pr(px(20.))
            .pb(px(44.))
            .gap(px(20.))
            .child(self.tab_bar(cx))
            .child(content)
    }
}

impl Render for Vespera {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let search = crate::text_fields::search(window, cx);
        let draft = crate::text_fields::draft(window, cx);
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
                            .child(icon("folder-01", 18., MUTED))
                            .child(muted("advanced-course  /  coder-cli", 14.)),
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
                    .child(sidebar::sidebar(sidebar_width, &search, cx))
                    .child(chat::chat(chat_width, &draft))
                    .child(self.inspector(cx)),
            )
    }
}
