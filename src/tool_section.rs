use std::time::Instant;

use crate::{history_cards::text, theme::*};
use gpui_kit::base::Button;
use gpui_kit::*;
use serde_json::Value;

fn code_source(content: &str, language: &str) -> String {
    let length = content
        .split(|ch| ch != '`')
        .map(str::len)
        .max()
        .unwrap_or(0);
    let fence = "`".repeat(length.max(2) + 1);
    format!("{fence}{language}\n{content}\n{fence}")
}

fn code_panel(id: SharedString, content: String, language: &'static str, width: f32) -> Div {
    div().w_full().min_w_0().child(
        crate::markdown::markdown(id, code_source(&content, language)).code_block_actions(
            move |block, window, _| crate::markdown::code_header(block, px(width), window),
        ),
    )
}

fn arguments(args: &Value) -> (String, &'static str) {
    if let Some(command) = args["command"].as_str() {
        (command.to_owned(), "sh")
    } else if let Some(raw) = args.as_str() {
        (raw.to_owned(), "text")
    } else {
        (
            serde_json::to_string_pretty(args).unwrap_or_default(),
            "json",
        )
    }
}

#[derive(Clone)]
pub struct ToolEntry {
    pub call: Value,
    pub result: Option<Value>,
    pub pending: bool,
    pub approval: Option<crate::chat_data::Approval>,
}

impl ToolEntry {
    fn status(&self) -> &'static str {
        match self.result.as_ref().map(|part| {
            if text(part, "part_kind") == "retry-prompt" {
                "failed"
            } else {
                text(part, "outcome")
            }
        }) {
            Some("failed" | "retried") => "执行失败",
            Some("denied") => "已拒绝",
            Some("interrupted") => "已中断",
            None if self.pending && text(&self.call, "tool_name") == "ask_user_question" => {
                "待确认"
            }
            None if self.pending => "进行中",
            None => "已中断",
            _ => "已完成",
        }
    }

    fn summary(&self) -> Div {
        let args = self.call["args"]
            .as_str()
            .and_then(|raw| serde_json::from_str::<Value>(raw).ok());
        let args = args.as_ref().unwrap_or(&self.call["args"]);
        let target = ["path", "command", "query"]
            .into_iter()
            .find_map(|key| args[key].as_str())
            .unwrap_or_default();
        row()
            .gap(px(8.))
            .child(badge(self.status()))
            .child(label(text(&self.call, "tool_name").to_owned(), 14.).font(emphasis_font()))
            .child(muted(target.to_owned(), 12.).min_w_0().truncate())
    }
}

pub fn badge(status: &'static str) -> Div {
    let (bg, ink) = match status {
        "已完成" => (0xd2f8e3, 0x27976c),
        "待确认" | "进行中" => (0xfffaee, 0xc87a17),
        "执行失败" | "已拒绝" => (0xffeeee, 0xc44f59),
        _ => (FIELD, MUTED),
    };
    label(status, 12.)
        .px(px(6.))
        .py(px(3.))
        .rounded(px(DETAIL_RADIUS))
        .bg(rgb(bg))
        .text_color(rgb(ink))
        .flex_shrink_0()
}

pub fn arrow(expanded: bool) -> Svg {
    icon("arrow-right-01", 16., MUTED).with_transformation(Transformation::rotate(radians(
        if expanded {
            std::f32::consts::FRAC_PI_2
        } else {
            0.
        },
    )))
}

#[derive(IntoElement)]
pub struct Tools {
    pub id: SharedString,
    pub entries: Vec<ToolEntry>,
    pub workspace: Option<String>,
    pub width: f32,
}

impl RenderOnce for Tools {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(self.id.clone(), cx, |_, _| false);
        let expanded = *state.read(cx);
        let latest = self
            .entries
            .last()
            .map(|entry| format!("{}{}", text(&entry.call, "tool_call_id"), entry.status()))
            .unwrap_or_default();
        let motion = window.use_keyed_state(
            SharedString::from(format!("{}-motion", self.id)),
            cx,
            |_, _| (latest.clone(), Instant::now()),
        );
        if motion.read(cx).0 != latest {
            motion.update(cx, |state, _| *state = (latest, Instant::now()));
        }
        let time = motion.read(cx).1.elapsed().as_secs_f32();
        let animate = !expanded && !cx.reduce_motion() && time < 0.25 && self.entries.len() > 1;
        let offset = if animate {
            32. * (1. - (time / 0.25).min(1.))
        } else {
            0.
        };
        if animate {
            window.request_animation_frame();
        }
        let summary = self.entries.last().map(|entry| entry.summary());
        let previous = self
            .entries
            .iter()
            .rev()
            .nth(1)
            .map(|entry| entry.summary());
        chat_card()
            .px(px(15.))
            .py(px(11.))
            .gap(px(6.))
            .child(
                Button::new(self.id.clone())
                    .accessibility_label("展开或收起工具调用")
                    .w_full()
                    .h(px(32.))
                    .justify_start()
                    .items_center()
                    .gap(px(8.))
                    .child(if expanded {
                        label("工具调用", 14.).flex_1().into_any_element()
                    } else {
                        div()
                            .flex_1()
                            .min_w_0()
                            .h(px(32.))
                            .relative()
                            .overflow_hidden()
                            .children(previous.filter(|_| animate).map(|item| {
                                item.absolute().top(px(offset - 32.)).w_full().h(px(32.))
                            }))
                            .children(
                                summary.map(|item| {
                                    item.absolute().top(px(offset)).w_full().h(px(32.))
                                }),
                            )
                            .into_any_element()
                    })
                    .child(arrow(expanded))
                    .on_click(move |_, _, cx| {
                        state.update(cx, |open, cx| {
                            *open = !*open;
                            cx.notify();
                        })
                    }),
            )
            .children(if expanded {
                self.entries
                    .into_iter()
                    .enumerate()
                    .map(|(index, entry)| {
                        ToolRow {
                            id: format!("{}-{index}", self.id).into(),
                            entry,
                            workspace: self.workspace.clone(),
                            width: self.width,
                        }
                        .into_any_element()
                    })
                    .collect::<Vec<_>>()
            } else {
                vec![]
            })
    }
}

#[derive(IntoElement)]
struct ToolRow {
    id: SharedString,
    entry: ToolEntry,
    workspace: Option<String>,
    width: f32,
}

impl RenderOnce for ToolRow {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(self.id.clone(), cx, |_, _| false);
        let open = *state.read(cx);
        let args = self.entry.call["args"]
            .as_str()
            .and_then(|raw| serde_json::from_str::<Value>(raw).ok());
        let args = args.as_ref().unwrap_or(&self.entry.call["args"]);
        let file_tool = matches!(
            text(&self.entry.call, "tool_name"),
            "read_file" | "write_file" | "edit_file"
        );
        let file = args["path"]
            .as_str()
            .filter(|_| file_tool)
            .and_then(|path| {
                self.workspace
                    .as_ref()
                    .map(|root| std::path::Path::new(root).join(path))
            });
        let output = self
            .entry
            .result
            .as_ref()
            .map(|part| &part["content"])
            .unwrap_or(&Value::Null);
        let detail = match output {
            Value::String(text) => text.clone(),
            Value::Null => String::new(),
            value => serde_json::to_string_pretty(value).unwrap_or_default(),
        };
        let (arguments, language) = arguments(args);
        let show_output = !detail.is_empty()
            && (file.is_none()
                || self
                    .entry
                    .result
                    .as_ref()
                    .is_some_and(|result| text(result, "outcome") != "success"));
        let args_id = format!("{}-arguments", self.id).into();
        let output_id = format!("{}-output", self.id).into();
        column()
            .gap(px(6.))
            .child(
                Button::new(self.id)
                    .accessibility_label("展开或收起工具结果")
                    .w_full()
                    .justify_start()
                    .py(px(3.))
                    .child(self.entry.summary().flex_1().min_w_0())
                    .child(arrow(open))
                    .on_click(move |_, _, cx| {
                        state.update(cx, |open, cx| {
                            *open = !*open;
                            cx.notify();
                        })
                    }),
            )
            .children(open.then(|| {
                column()
                    .gap(px(6.))
                    .pl(px(6.))
                    .children(
                        file.is_none()
                            .then(|| code_panel(args_id, arguments, language, self.width)),
                    )
                    .children(self.entry.approval.as_ref().map(|approval| {
                        muted(format!("{} · {}", approval.action, approval.reason), 12.)
                    }))
                    .children(file.map(|path| crate::file_tree::zed_button(path)))
                    .children(
                        show_output.then(|| code_panel(output_id, detail, "text", self.width)),
                    )
            }))
    }
}

#[cfg(test)]
mod tests {
    use super::{ToolEntry, arguments, code_source};
    use serde_json::json;

    #[test]
    fn tool_code_keeps_commands_and_output_literal() {
        assert_eq!(
            arguments(&json!({"command":"printf 'hello'", "timeout":10})),
            ("printf 'hello'".into(), "sh")
        );
        let (args, language) = arguments(&json!({"path":"src", "depth":2}));
        assert_eq!(language, "json");
        assert!(args.contains('\n'));
        let output = "log\n```\n# literal, not a heading";
        assert_eq!(
            code_source(output, "text"),
            format!("````text\n{output}\n````")
        );
    }

    #[test]
    fn tool_badges_follow_actual_result_and_pending_state() {
        let mut tool = ToolEntry {
            call: json!({"tool_name":"read_file","tool_call_id":"one"}),
            result: None,
            pending: true,
            approval: None,
        };
        assert_eq!(tool.status(), "进行中");
        tool.call["tool_name"] = json!("ask_user_question");
        assert_eq!(tool.status(), "待确认");
        tool.result = Some(json!({"outcome":"success"}));
        assert_eq!(tool.status(), "已完成");
        tool.result = Some(json!({"outcome":"denied"}));
        assert_eq!(tool.status(), "已拒绝");
        tool.result = None;
        tool.pending = false;
        assert_eq!(tool.status(), "已中断");
    }
}
