use crate::masked_scroll::MaskedScroll;
use crate::project_group::ProjectGroup;
use crate::theme::*;
use crate::{
    Vespera,
    chat_data::Summary,
    chat_state::{Chats, ListPhase},
};
use gpui_kit::base::input::InputState;
use gpui_kit::*;

const SEARCH_GAP: f32 = 16.;
const HEADER_HEIGHT: f32 = 36.;

fn session(
    id: SharedString,
    title: impl Into<SharedString>,
    time: impl Into<SharedString>,
    active: bool,
) -> Div {
    const HEIGHT: f32 = 35.;
    let content = row()
        .w_full()
        .h(px(if active { HEIGHT } else { 14. * 1.3 }))
        .px(px(10.))
        .gap(px(6.))
        .justify_between()
        .child(crate::sidebar_title::SidebarTitle {
            id,
            text: title.into(),
            active,
        })
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

fn account(usage: Option<(f64, f64)>) -> Div {
    card().h(px(56.)).p(px(12.)).justify_end().child(
        row()
            .justify_between()
            .h(px(32.))
            .child(
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
            )
            .child(usage_ring(usage)),
    )
}

pub fn sidebar(
    width: f32,
    search: &Entity<InputState>,
    chats: Option<&Chats>,
    usage: Option<(f64, f64)>,
    cx: &Context<Vespera>,
) -> Div {
    let query = search.read(cx).value().trim().to_lowercase();
    let content = if let Some(chats) = chats {
        live_groups(chats, &query, cx)
    } else {
        preview_groups(&query)
    };
    let new_chat = cx.listener(|this, _, window, cx| {
        crate::new_chat_dialog::open(this, window, cx);
    });
    column()
        .h_full()
        .w(px(width))
        .flex_shrink_0()
        .border_r_1()
        .border_color(rgb(BORDER))
        .pt(px(PANEL_TOP))
        .pl(px(20.))
        .pr(px(22.))
        .pb(px(PANEL_TOP))
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
                        .child(label("新聊天", 16.))
                        .id("new-chat")
                        .debug_selector(|| "new-chat".into())
                        .on_click(new_chat),
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
                .child(crate::text_fields::input_field(search)),
        )
        .child(MaskedScroll::new("session-history", 64., content))
        .child(account(usage).mt(px(20.)))
}

fn preview_groups(query: &str) -> Div {
    column().gap(px(10.)).children(
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
            ("Ani Bot", true, &[("设计桌面布局", "今天", false)][..]),
            ("其他", false, &[("审核工具权限", "11月4日", false)][..]),
        ]
        .into_iter()
        .filter_map(|(name, folder, sessions)| {
            let items: Vec<_> = sessions
                .iter()
                .filter(|(title, _, _)| {
                    name.contains(query) || title.to_lowercase().contains(query)
                })
                .map(|&(title, time, active)| {
                    session(
                        format!("preview:{name}:{title}").into(),
                        title,
                        time,
                        active,
                    )
                })
                .collect();
            (!items.is_empty()).then_some(ProjectGroup {
                id: name.into(),
                name: name.into(),
                folder,
                items,
            })
        }),
    )
}

pub fn matches_query(row: &Summary, query: &str) -> bool {
    row.project().to_lowercase().contains(query) || row.title().to_lowercase().contains(query)
}

fn live_groups(chats: &Chats, query: &str, cx: &Context<Vespera>) -> Div {
    let mut groups: Vec<(&Summary, Vec<Div>)> = Vec::new();
    for item in chats.rows.iter().filter(|item| matches_query(item, query)) {
        let active = chats.selected.as_deref() == Some(&item.conversation_id);
        let id = item.conversation_id.clone();
        let open = cx.listener(move |this, _, _, cx| {
            let refresh = this
                .chats
                .as_ref()
                .and_then(|chats| chats.selected.as_deref())
                == Some(&id);
            this.open_chat(id.clone(), refresh, cx);
        });
        let row = div().child(
            div()
                .id(SharedString::from(item.conversation_id.clone()))
                .child(session(
                    item.conversation_id.clone().into(),
                    item.title().into_owned(),
                    activity(item.updated_at.as_deref()),
                    active,
                ))
                .on_click(open),
        );
        if let Some((_, items)) = groups
            .iter_mut()
            .find(|(group, _)| group.work_path == item.work_path)
        {
            items.push(row);
        } else {
            groups.push((item, vec![row]));
        }
    }
    let mut content = column().gap(px(10.));
    if chats.list.needs_reload() {
        let message = if matches!(chats.list, ListPhase::Reconciling(_)) {
            "新建结果未确认"
        } else {
            "列表刷新失败"
        };
        content = content.child(reload_row(
            "reload-sidebar",
            message,
            cx.listener(|this, _, _, cx| this.reload_list(cx)),
        ));
    }
    content.children(groups.into_iter().map(|(group, items)| ProjectGroup {
        id: format!("project:{}", group.work_path.as_deref().unwrap_or_default()).into(),
        name: group.project().to_owned().into(),
        folder: group.work_path.is_some(),
        items,
    }))
}

pub fn reload_row(
    id: &'static str,
    message: &'static str,
    reload: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Div {
    row()
        .gap(px(8.))
        .flex_wrap()
        .child(muted(message, 12.))
        .child(
            label("重新读取", 12.)
                .text_color(rgb(PRIMARY))
                .id(id)
                .on_click(reload),
        )
}

fn activity(value: Option<&str>) -> String {
    use chrono::{DateTime, Local};
    let Some(time) = value.and_then(|value| DateTime::parse_from_rfc3339(value).ok()) else {
        return String::new();
    };
    let time = time.with_timezone(&Local);
    let now = Local::now();
    match (now.date_naive() - time.date_naive()).num_days() {
        0 if (0..60).contains(&(now - time).num_seconds()) => "刚刚".into(),
        0 => "今天".into(),
        1 => "昨天".into(),
        _ => time.format("%-m月%-d日").to_string(),
    }
}
