use gpui_kit::component::{Root, Theme, ThemeMode};
use gpui_kit::*;
use std::{path::PathBuf, sync::Arc};
use vespera::{InspectorTab, Vespera, assets::Assets};

fn click(window: &mut Window, cx: &mut App, position: Point<Pixels>) {
    for event in [
        PlatformInput::MouseDown(MouseDownEvent {
            button: MouseButton::Left,
            position,
            modifiers: Modifiers::default(),
            click_count: 1,
            first_mouse: false,
        }),
        PlatformInput::MouseUp(MouseUpEvent {
            button: MouseButton::Left,
            position,
            modifiers: Modifiers::default(),
            click_count: 1,
        }),
    ] {
        window.dispatch_event(event, cx);
    }
}

fn drag(window: &mut Window, cx: &mut App, start: Point<Pixels>, end: Point<Pixels>) {
    for event in [
        PlatformInput::MouseDown(MouseDownEvent {
            button: MouseButton::Left,
            position: start,
            modifiers: Modifiers::default(),
            click_count: 1,
            first_mouse: false,
        }),
        PlatformInput::MouseMove(MouseMoveEvent {
            position: end,
            pressed_button: Some(MouseButton::Left),
            modifiers: Modifiers::default(),
        }),
        PlatformInput::MouseUp(MouseUpEvent {
            button: MouseButton::Left,
            position: end,
            modifiers: Modifiers::default(),
            click_count: 1,
        }),
    ] {
        window.dispatch_event(event, cx);
    }
}

fn main() -> anyhow::Result<()> {
    let output = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("verification");
    std::fs::create_dir_all(&output)?;
    let native = platform::current_platform(true);
    let mut cx = HeadlessAppContext::with_platform(
        native.text_system(),
        Arc::new(Assets),
        platform::current_headless_renderer,
    );
    cx.update(|cx| {
        init(cx);
        Theme::change(ThemeMode::Light, None, cx);
    });
    let mut view = None;
    let window = cx.open_window(size(px(1440.), px(960.)), |window, cx| {
        let entity = cx.new(|_| Vespera::default());
        view = Some(entity.clone());
        cx.new(|cx| Root::new(entity, window, cx))
    })?;
    let view = view.expect("Vespera view created");
    let mut expected_left = None;
    for (index, tab) in InspectorTab::ALL.into_iter().enumerate() {
        if index > 0 {
            cx.update_window(window.into(), |_, window, cx| {
                let x = if tab == InspectorTab::Artifacts {
                    1200.
                } else {
                    1350.
                };
                click(window, cx, point(px(x), px(104.)));
            })?;
        }
        for _ in 0..3 {
            cx.run_until_parked();
            cx.update_window(window.into(), |_, window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            })?;
        }
        cx.update(|cx| assert_eq!(view.read(cx).tab, tab, "Tab pointer interaction"));
        let name = match tab {
            InspectorTab::Status => "status",
            InspectorTab::Artifacts => "artifacts",
            InspectorTab::Memory => "memory",
        };
        let screenshot = cx.capture_screenshot(window.into())?;
        let left_width = screenshot.width() * 956 / 1440;
        let left: Vec<_> = screenshot
            .enumerate_pixels()
            .filter(|(x, _, _)| *x < left_width)
            .map(|(_, _, pixel)| pixel.0)
            .collect();
        if let Some(expected) = &expected_left {
            assert_eq!(&left, expected, "Tabs must preserve the first two columns");
        } else {
            expected_left = Some(left);
        }
        screenshot.save(output.join(format!("{name}.png")))?;
        println!("Verified {name}: clicked tab and captured 1440x960 frame");
    }
    cx.update_window(window.into(), |_, window, cx| {
        let copy = gpui_kit::base::test_support::find(window, &[], &"copy-code".into())
            .expect("code copy button");
        click(window, cx, copy.bounds().center());
        let text = cx.read_from_clipboard().and_then(|item| item.text());
        assert_eq!(
            text.as_deref(),
            Some("uv run pytest tests/test_sessions.py")
        );
        println!("Verified code copy writes the command to the clipboard");
    })?;
    cx.update_window(window.into(), |_, window, _| window.activate_window())?;
    cx.run_until_parked();
    cx.update_window(window.into(), |_, window, cx| {
        drag(
            window,
            cx,
            point(px(277.), px(835.)),
            point(px(384.), px(835.)),
        );
    })?;
    cx.update_window(window.into(), |_, window, cx| {
        cx.write_to_clipboard(ClipboardItem::new_string("第一行草稿\n第二行草稿".into()));
        window.dispatch_keystroke(Keystroke::parse("cmd-v").unwrap(), cx);
    })?;
    cx.run_until_parked();
    cx.update_window(window.into(), |_, window, cx| {
        window.draw(cx).clear(cx);
        drag(
            window,
            cx,
            point(px(380.), px(859.)),
            point(px(277.), px(835.)),
        );
        cx.write_to_clipboard(ClipboardItem::new_string("clipboard sentinel".into()));
        window.dispatch_keystroke(Keystroke::parse("cmd-c").unwrap(), cx);
        let text = cx.read_from_clipboard().and_then(|item| item.text());
        assert_eq!(text.as_deref(), Some("第一行草稿\n第二行草稿"));
        window.draw(cx).clear(cx);
    })?;
    let selection = cx.capture_screenshot(window.into())?;
    let scale = selection.width() as f32 / 1440.;
    for band in [824. ..845., 848. ..869.] {
        assert!(
            selection.enumerate_pixels().any(|(x, y, pixel)| {
                (277. ..301.).contains(&(x as f32 / scale))
                    && band.contains(&(y as f32 / scale))
                    && pixel.0[..3] == [255, 242, 235]
            }),
            "Dragging across the draft must paint each selected row in #FFF2EB"
        );
    }
    println!("Verified draft drag selection color");
    for (name, x) in [("sidebar", 140.), ("chat", 600.), ("inspector", 1200.)] {
        let mut times = Vec::new();
        for frame in 0..180 {
            let start = std::time::Instant::now();
            cx.update_window(window.into(), |_, window, cx| {
                for _ in 0..3 {
                    window.dispatch_event(
                        PlatformInput::Scroll(ScrollEvent {
                            position: point(px(x), px(400.)),
                            delta: ScrollDelta::Lines(point(
                                0.,
                                if frame % 24 < 12 { -0.5 } else { 0.5 },
                            )),
                            ..Default::default()
                        }),
                        cx,
                    );
                }
                window.draw(cx).clear(cx);
            })?;
            times.push(start.elapsed().as_secs_f64() * 1000.);
        }
        times.sort_by(f64::total_cmp);
        let over_budget = times.iter().filter(|&&ms| ms > 1000. / 60.).count();
        println!(
            "{name}: CPU dispatch+draw p50={:.2}ms p95={:.2}ms max={:.2}ms, over 16.67ms={over_budget}/{}",
            times[90],
            times[171],
            times[179],
            times.len()
        );
    }
    for (width, height) in [(1140., 800.), (1280., 800.), (1920., 1080.)] {
        let window = cx.open_window(size(px(width), px(height)), |window, cx| {
            let view = cx.new(|_| Vespera::preview(InspectorTab::Memory));
            cx.new(|cx| Root::new(view, window, cx))
        })?;
        for _ in 0..3 {
            cx.run_until_parked();
            cx.update_window(window.into(), |_, window, cx| {
                window.refresh();
                window.draw(cx).clear(cx);
            })?;
        }
        cx.capture_screenshot(window.into())?
            .save(output.join(format!("memory-{width}x{height}.png")))?;
        println!("Verified layout at {width}x{height}");
        if width == 1140. {
            verify_chat_scroll_axis(&mut cx, window.into())?;
            let before = cx.capture_screenshot(window.into())?;
            let scale = before.width() as f32 / width;
            let (x, y, _) = before
                .enumerate_pixels()
                .find(|(x, _, pixel)| {
                    (*x as f32 / scale - 767.).abs() < 4. && pixel.0[..3] == [250, 148, 93]
                })
                .expect("visible orange chat scrollbar thumb");
            let start = point(px((x as f32 + 0.5) / scale), px(y as f32 / scale + 4.));
            let end = point(start.x, start.y + px(115.));
            cx.update_window(window.into(), |_, window, cx| {
                drag(window, cx, start, end);
            })?;
            for _ in 0..3 {
                cx.run_until_parked();
                cx.update_window(window.into(), |_, window, cx| {
                    window.refresh();
                    window.draw(cx).clear(cx);
                })?;
            }
            let after = cx.capture_screenshot(window.into())?;
            let changed = before.enumerate_pixels().any(|(x, y, pixel)| {
                x > 600 && x < 1400 && y > 160 && y < 600 && *pixel != *after.get_pixel(x, y)
            });
            assert!(
                changed,
                "Dragging the chat thumb must move the conversation"
            );
            after.save(output.join("chat-dragged.png"))?;
            println!("Verified chat scrollbar drag changes conversation content");
        }
    }
    Ok(())
}

fn verify_chat_scroll_axis(
    cx: &mut HeadlessAppContext,
    window: AnyWindowHandle,
) -> anyhow::Result<()> {
    let mut frame = |x, y, touch_phase| -> anyhow::Result<_> {
        cx.update_window(window, |_, window, cx| {
            window.dispatch_event(
                PlatformInput::Scroll(ScrollEvent {
                    position: point(px(480.), px(400.)),
                    delta: ScrollDelta::Pixels(point(px(x), px(y))),
                    touch_phase,
                    ..Default::default()
                }),
                cx,
            );
        })?;
        for _ in 0..3 {
            cx.run_until_parked();
            cx.update_window(window, |_, window, cx| {
                window.draw(cx).clear(cx);
            })?;
        }
        cx.capture_screenshot(window)
    };
    let top = frame(0., 0., TouchPhase::Started)?;
    let bottom = frame(0., -500., TouchPhase::Moved)?;
    let horizontal = frame(500., 0., TouchPhase::Moved)?;
    let downward = frame(0., -500., TouchPhase::Moved)?;
    assert_ne!(top, bottom, "Vertical scrolling must move the conversation");
    assert!(
        bottom == horizontal,
        "Horizontal input must leave the conversation in place"
    );
    assert!(
        bottom == downward,
        "Continuing down should stay at the bottom"
    );
    let returned = frame(0., 500., TouchPhase::Moved)?;
    assert!(
        top == returned,
        "Vertical input must still return to the top"
    );
    Ok(())
}
