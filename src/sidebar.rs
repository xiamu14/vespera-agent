use crate::masked_scroll::MaskedScroll;
use crate::project_group::ProjectGroup;
use crate::theme::*;
use gpui_kit::base::input::InputState;
use gpui_kit::*;

const SEARCH_GAP: f32 = 16.;
const HEADER_HEIGHT: f32 = INSPECTOR_TAB_HEIGHT - SEARCH_GAP;

fn session(title: &'static str, time: &'static str, active: bool) -> Div {
    const HEIGHT: f32 = 35.;
    let content = row()
        .w_full()
        .h(px(HEIGHT))
        .px(px(10.))
        .gap(px(12.))
        .justify_between()
        .child(
            label(title, 14.)
                .min_w_0()
                .whitespace_nowrap()
                .overflow_hidden(),
        )
        .child(
            label(time, 12.)
                .text_color(rgb(0x999999))
                .flex_shrink_0()
                .whitespace_nowrap(),
        );
    if active {
        content
            .rounded(limited_radius(CONTROL_RADIUS, HEIGHT))
            .bg(rgb(FIELD))
    } else {
        content
    }
}

fn account() -> Div {
    card()
        .h(px(94.))
        .p(px(12.))
        .gap(px(12.))
        .child(
            row()
                .justify_between()
                .h(px(26.))
                .child(muted("今日消耗", 14.))
                .child(label("$12", 14.)),
        )
        .child(
            row().justify_between().h(px(32.)).child(
                row()
                    .gap(px(8.))
                    .child(
                        row()
                            .size(px(28.))
                            .justify_center()
                            .rounded_full()
                            .bg(rgb(PRIMARY))
                            .child(label("维", 14.).text_color(rgb(0xffffff))),
                    )
                    .child(label("小维拉", 14.)),
            ),
        )
}

pub fn sidebar(width: f32, search: &Entity<InputState>, cx: &App) -> Div {
    let query = search.read(cx).value();
    let query = query.trim().to_lowercase();
    column()
        .h_full()
        .w(px(width))
        .flex_shrink_0()
        .border_r_1()
        .border_color(rgb(BORDER))
        .pt(px(PANEL_TOP))
        .pl(px(20.))
        .pr(px(22.))
        .pb(px(44.))
        .child(
            row()
                .w_full()
                .h(px(HEADER_HEIGHT))
                .flex_shrink_0()
                .justify_between()
                .child(label("聊天", 20.).font(emphasis_font()))
                .child(
                    row()
                        .h(px(HEADER_HEIGHT))
                        .p(px(10.))
                        .py(px(6.))
                        .flex_shrink_0()
                        .gap(px(4.))
                        .rounded(limited_radius(CONTROL_RADIUS, HEADER_HEIGHT))
                        .border_1()
                        .border_color(rgb(PRIMARY))
                        .bg(rgb(PRIMARY_WASH))
                        .text_color(rgb(PRIMARY))
                        .child(icon("new-chat-plus", 15., PRIMARY))
                        .child(label("新聊天", 16.).font(emphasis_font())),
                ),
        )
        .child(
            row()
                .w_full()
                .h(px(40.))
                .my(px(SEARCH_GAP))
                .flex_shrink_0()
                .px(px(10.))
                .gap(px(8.))
                .rounded(px(CONTROL_RADIUS))
                .bg(rgb(FIELD))
                .border_1()
                .border_color(rgb(0xeeeeef))
                .child(icon("search-01", 18., MUTED))
                .child(crate::text_fields::search_field(search)),
        )
        .child(MaskedScroll::new(
            "session-history",
            64.,
            column().gap(px(14.)).children(
                [
                    (
                        "coder-cli",
                        true,
                        &[
                            ("修复会话恢复", "刚刚", true),
                            ("项目上下文缓存", "今天", false),
                        ][..],
                    ),
                    ("weather-mcp", true, &[("添加天气 MCP", "昨天", false)][..]),
                    ("vespera", true, &[("设计桌面布局", "今天", false)][..]),
                    ("其他", false, &[("审核工具权限", "11月4日", false)][..]),
                ]
                .into_iter()
                .filter_map(|(name, folder, sessions)| {
                    let items: Vec<_> = sessions
                        .iter()
                        .filter(|(title, _, _)| {
                            name.contains(&query) || title.to_lowercase().contains(&query)
                        })
                        .map(|&(title, time, active)| session(title, time, active))
                        .collect();
                    (!items.is_empty()).then_some(ProjectGroup {
                        name,
                        folder,
                        has_active_chat: sessions.iter().any(|&(_, _, active)| active),
                        items,
                    })
                }),
            ),
        ))
        .child(account().mt(px(20.)))
}
