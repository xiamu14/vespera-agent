use crate::{
    chat_data::request_payload, inspector::DirectoryEntry, system_notification::notify, theme::*,
};
use gpui_kit::{base::Button, *};
use serde_json::json;
use std::{collections::HashSet, path::PathBuf, time::Instant};

pub fn zed_button(path: PathBuf) -> Button {
    Button::new(SharedString::from(format!("zed-{}", path.display())))
        .accessibility_label("在 Zed 中打开")
        .justify_start()
        .child(label("在 Zed 中打开", 12.).text_color(rgb(PRIMARY)))
        .on_click(move |_, _, cx| open_zed(path.clone(), cx))
}

fn open_zed(path: PathBuf, cx: &mut App) {
    let task = cx.background_executor().spawn(async move {
        std::process::Command::new("/usr/bin/open")
            .arg("-a")
            .arg("Zed")
            .arg("--")
            .arg(path)
            .status()
    });
    cx.spawn(async move |cx| match task.await {
        Ok(status) if status.success() => {}
        _ => {
            let _ = cx.update(|_| {
                notify(
                    "无法在 Zed 中打开文件",
                    "请确认已安装 Zed，且文件仍然存在。",
                )
            });
        }
    })
    .detach();
}

struct TreeState {
    open: HashSet<String>,
    entries: Vec<DirectoryEntry>,
    loaded: HashSet<String>,
    loading: HashSet<String>,
}

#[derive(IntoElement)]
pub struct FileTree {
    pub conversation_id: String,
    pub root: PathBuf,
    pub entries: Vec<DirectoryEntry>,
    pub app: WeakEntity<crate::Vespera>,
}

impl RenderOnce for FileTree {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let state = window.use_keyed_state(
            SharedString::from(format!(
                "file-tree-{}-{}",
                self.conversation_id,
                self.root.display()
            )),
            cx,
            |_, _| TreeState {
                open: HashSet::from([String::new()]),
                loaded: self
                    .entries
                    .iter()
                    .filter(|entry| entry.folder && entry.depth < 2)
                    .map(|entry| entry.path.clone())
                    .collect(),
                entries: self.entries.clone(),
                loading: HashSet::new(),
            },
        );
        // Merge refreshed root listings while preserving expansion choices.
        state.update(cx, |state, _| {
            let fresh: HashSet<_> = self
                .entries
                .iter()
                .map(|entry| entry.path.as_str())
                .collect();
            state
                .entries
                .retain(|entry| entry.depth > 2 || fresh.contains(entry.path.as_str()));
            for entry in &self.entries {
                if !state.entries.iter().any(|old| old.path == entry.path) {
                    state.entries.push(entry.clone());
                }
            }
        });
        let rows = ordered_rows(&state.read(cx).entries, &state.read(cx).open);
        let mut content = column();
        for (entry, guides) in rows {
            content = content.child(TreeRow {
                entry,
                guides,
                state: state.clone(),
                root: self.root.clone(),
                conversation_id: self.conversation_id.clone(),
                app: self.app.clone(),
            });
        }
        content
    }
}

fn ordered_rows(
    entries: &[DirectoryEntry],
    open: &HashSet<String>,
) -> Vec<(DirectoryEntry, Vec<bool>)> {
    fn visit(
        entry: &DirectoryEntry,
        guides: Vec<bool>,
        entries: &[DirectoryEntry],
        open: &HashSet<String>,
        rows: &mut Vec<(DirectoryEntry, Vec<bool>)>,
    ) {
        rows.push((entry.clone(), guides.clone()));
        if !entry.folder || !open.contains(&entry.path) {
            return;
        }
        let mut children: Vec<_> = entries
            .iter()
            .filter(|child| {
                !child.path.is_empty()
                    && std::path::Path::new(&child.path).parent()
                        == Some(std::path::Path::new(&entry.path))
            })
            .collect();
        children
            .sort_by_key(|child| (!child.folder, child.name.to_lowercase(), child.name.clone()));
        for (index, child) in children.iter().enumerate() {
            let mut next = guides.clone();
            next.push(index + 1 < children.len());
            visit(child, next, entries, open, rows);
        }
    }
    let mut rows = Vec::new();
    if let Some(root) = entries.iter().find(|entry| entry.path.is_empty()) {
        visit(root, Vec::new(), entries, open, &mut rows);
    }
    rows
}

#[derive(IntoElement)]
struct TreeRow {
    entry: DirectoryEntry,
    guides: Vec<bool>,
    state: Entity<TreeState>,
    root: PathBuf,
    conversation_id: String,
    app: WeakEntity<crate::Vespera>,
}

impl RenderOnce for TreeRow {
    fn render(self, window: &mut Window, cx: &mut App) -> impl IntoElement {
        let key = format!("tree-row-{}-{}", self.conversation_id, self.entry.path);
        let hover = window.use_keyed_state(SharedString::from(key.clone()), cx, |_, _| {
            (false, Instant::now(), 0.0f32)
        });
        let (hovering, start, from) = *hover.read(cx);
        let progress = if cx.reduce_motion() {
            1.
        } else {
            (start.elapsed().as_secs_f32() / 0.14).min(1.)
        };
        let opacity = from + ((if hovering { 1. } else { 0. }) - from) * progress;
        if progress < 1. {
            window.request_animation_frame();
        }
        let entry = self.entry;
        let path = entry.path.clone();
        let state = self.state.clone();
        let root = self.root;
        let conversation_id = self.conversation_id;
        let app = self.app;
        Button::new(SharedString::from(key))
            .accessibility_label(entry.name.clone())
            .w_full()
            .h(px(28.))
            .flex_shrink_0()
            .justify_start()
            .items_center()
            .relative()
            .pr(px(8.))
            .gap(px(12.))
            .pl(px(
                entry.depth as f32 * 28. + if entry.folder { 0. } else { 12. }
            ))
            .rounded(limited_radius(CONTROL_RADIUS, 28.))
            .bg(rgb(FIELD).opacity(opacity))
            .on_hover(move |on, _, cx| {
                hover.update(cx, |value, cx| {
                    *value = (*on, Instant::now(), opacity);
                    cx.notify();
                })
            })
            .children(
                self.guides
                    .iter()
                    .enumerate()
                    .filter_map(|(level, continues)| {
                        let own = level + 1 == self.guides.len();
                        let asset = if own {
                            if *continues {
                                "icons/tree-branch.svg"
                            } else {
                                "icons/tree-last.svg"
                            }
                        } else if *continues {
                            "icons/tree-line.svg"
                        } else {
                            return None;
                        };
                        Some(
                            svg()
                                .path(asset)
                                .size(px(28.))
                                .text_color(rgb(PRIMARY).opacity(0.7))
                                .absolute()
                                .top_0()
                                .left(px(level as f32 * 28.)),
                        )
                    }),
            )
            .children(
                entry
                    .folder
                    .then(|| icon("folder-01", 18., MUTED).flex_shrink_0()),
            )
            .child(muted(entry.name, 14.).min_w_0().truncate())
            .on_click(move |event, window, cx| {
                if event.click_count() == 2 {
                    if entry.folder {
                        state.update(cx, |state, cx| {
                            if !state.open.remove(&path) {
                                state.open.insert(path.clone());
                            }
                            cx.notify();
                        });
                    }
                    let _ = app.update(cx, |app, cx| {
                        app.reference_path(
                            &conversation_id,
                            &root.join(&path),
                            entry.folder,
                            window,
                            cx,
                        )
                    });
                    return;
                }
                if !entry.folder {
                    return;
                }
                let needs_load = state.update(cx, |state, cx| {
                    if state.open.remove(&path) {
                        cx.notify();
                        return false;
                    }
                    state.open.insert(path.clone());
                    let load = !state.loaded.contains(&path) && !state.loading.contains(&path);
                    if load {
                        state.loading.insert(path.clone());
                    }
                    cx.notify();
                    load
                });
                if !needs_load {
                    return;
                }
                let payload =
                    json!({"command":"directory","conversation_id":conversation_id,"path":path});
                let task = cx
                    .background_executor()
                    .spawn(async move { request_payload::<Vec<DirectoryEntry>>(payload) });
                let state = state.downgrade();
                let path = path.clone();
                cx.spawn(async move |cx| {
                    let result = task.await;
                    let _ = state.update(cx, |state, cx| {
                        state.loading.remove(&path);
                        match result {
                            Ok(entries) if state.entries.len() + entries.len() > 1000 => {
                                state.open.remove(&path);
                                notify("目录展开已达上限", "本次最多展示 1000 个目录项。");
                            }
                            Ok(entries) => {
                                state.loaded.insert(path);
                                state.entries.extend(entries);
                            }
                            Err(error) => {
                                state.open.remove(&path);
                                notify("无法展开目录", error.message());
                            }
                        }
                        cx.notify();
                    });
                })
                .detach();
            })
    }
}

#[cfg(test)]
mod tests {
    use super::ordered_rows;
    use crate::inspector::DirectoryEntry;
    use std::collections::HashSet;

    #[test]
    fn tree_keeps_sibling_order_and_guides_when_folders_toggle() {
        let entries: Vec<DirectoryEntry> = [
            ("", true),
            ("z.txt", false),
            ("backend/nested/file.rs", false),
            ("backend/nested", true),
            ("backend", true),
            ("docs", true),
        ]
        .into_iter()
        .map(|(path, folder)| DirectoryEntry {
            path: path.into(),
            name: path.rsplit('/').next().unwrap().into(),
            depth: if path.is_empty() {
                0
            } else {
                path.split('/').count()
            },
            folder,
        })
        .collect();
        let mut open = HashSet::from([String::new()]);
        let rows = ordered_rows(&entries, &open);
        assert_eq!(
            rows.iter()
                .map(|(entry, _)| entry.path.as_str())
                .collect::<Vec<_>>(),
            ["", "backend", "docs", "z.txt"]
        );
        assert_eq!(rows.last().unwrap().1, [false]);
        open.insert("backend".into());
        open.insert("backend/nested".into());
        let rows = ordered_rows(&entries, &open);
        assert_eq!(
            rows.iter()
                .map(|(entry, _)| entry.path.as_str())
                .collect::<Vec<_>>(),
            [
                "",
                "backend",
                "backend/nested",
                "backend/nested/file.rs",
                "docs",
                "z.txt"
            ]
        );
        assert_eq!(rows[3].1, [true, false, false]);
        open.remove("backend");
        assert_eq!(ordered_rows(&entries, &open).len(), 4);
        open.clear();
        assert_eq!(ordered_rows(&entries, &open).len(), 1);
    }
}
