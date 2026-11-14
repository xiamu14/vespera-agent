use super::trigger;
#[test]
fn triggers_respect_prefix_and_utf8_cursor() {
    assert_eq!(trigger("/rev", 4).unwrap().query, "rev");
    assert_eq!(trigger("text /rev", 9).unwrap().range, 5..9);
    let inline = "请检查 /rev 这个项目";
    let cursor = "请检查 /rev".len();
    assert_eq!(trigger(inline, cursor).unwrap().query, "rev");
    assert!(trigger("path/rev", 8).is_none());
    assert!(trigger("me@example", 10).is_none());
    for value in [r"\/reviewer", r"查看 \@笔记"] {
        assert!(trigger(value, value.len()).is_none());
    }
    let value = "查看 @笔记";
    let token = trigger(value, value.len()).unwrap();
    assert_eq!(&value[token.range], "@笔记");
}

#[cfg(all(debug_assertions, feature = "visual-check"))]
#[test]
fn composer_menu_aligns_scrolls_and_accepts_keyboard_selection() -> anyhow::Result<()> {
    use super::*;
    use gpui_kit::component::{Theme, ThemeMode};
    let mut cx = TestAppContext::single();
    cx.update(|cx| {
        init(cx);
        Theme::change(ThemeMode::Light, None, cx);
    });
    struct TestView {
        app: Entity<Vespera>,
        draft: Entity<TextareaState>,
        _keys: Subscription,
    }
    impl Render for TestView {
        fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
            let popup = self.app.update(cx, |app, cx| {
                menu(app, &self.draft, cx).map(IntoElement::into_any_element)
            });
            column()
                .size_full()
                .child(
                    crate::masked_scroll::MaskedScroll::new("test-history", 0., div().h(px(1400.)))
                        .scroll_handle(self.app.read(cx).timeline.scroll.clone()),
                )
                .child(
                    div().relative().w_full().children(popup).child(
                        composer_surface()
                            .w_full()
                            .h(px(108.))
                            .p(px(14.))
                            .debug_selector(|| "composer-input".into())
                            .child(crate::text_fields::draft_field(&self.draft)),
                    ),
                )
        }
    }
    let window = cx.open_window(size(px(1140.), px(800.)), |window, cx| {
        let draft = cx.new(|cx| TextareaState::new(window, cx));
        draft.update(cx, |draft, cx| {
            draft.set_value("/", window, cx);
            draft.set_cursor_position(gpui_kit::base::input::Position::new(0, 1), window, cx);
        });
        let app = cx.new(|_| {
            let mut app = Vespera::default();
            app.composer_menu = ComposerMenu {
                target: Some((draft.entity_id(), trigger("/", 1).unwrap())),
                choices: Some(Choices::Slash {
                    roles: Vec::new(),
                    skills: (0..20)
                        .map(|index| Choice {
                            name: format!("skill-{index}"),
                            description: "Description".into(),
                        })
                        .collect(),
                }),
                ..Default::default()
            };
            app
        });
        let keys = app.update(cx, |_, cx| intercept_keys(&draft, window, cx));
        TestView {
            app,
            draft,
            _keys: keys,
        }
    });
    cx.run_until_parked();
    let history = window.update(&mut cx, |view, _, cx| {
        view.app.read(cx).timeline.scroll.clone()
    })?;
    let mut visual = VisualTestContext::from_window(window.into(), &cx);
    let popup = visual.debug_bounds("composer-choices").unwrap();
    let input = visual.debug_bounds("composer-input").unwrap();
    assert_eq!(popup.left(), input.left());
    assert_eq!(popup.size.width, input.size.width);
    let viewport = visual.debug_bounds("composer-menu-viewport").unwrap();
    assert_eq!(
        viewport.top() - popup.top(),
        CHAT_SECTION_PADDING_Y + px(1.)
    );
    assert_eq!(
        popup.bottom() - viewport.bottom(),
        CHAT_SECTION_PADDING_Y + px(1.)
    );
    assert_eq!(
        viewport.left() - popup.left(),
        CHAT_SECTION_PADDING_X + px(1.)
    );
    let scroll = window.update(&mut cx, |view, _, cx| {
        let menu = &view.app.read(cx).composer_menu;
        assert_eq!(menu.selected, 0);
        menu.scroll.clone()
    })?;
    let offset = history.offset();
    let wheel = |cx: &mut TestAppContext, y| -> anyhow::Result<()> {
        window.update(cx, |_, window, cx| {
            window.dispatch_event(
                PlatformInput::Scroll(ScrollEvent {
                    position: point(px(80.), px(y)),
                    delta: ScrollDelta::Lines(point(0., -4.)),
                    ..Default::default()
                }),
                cx,
            );
            window.refresh();
        })?;
        cx.run_until_parked();
        Ok(())
    };
    wheel(&mut cx, 550.)?;
    assert_eq!(history.offset(), offset);

    assert!(
        scroll.offset().y < px(0.),
        "The visible choices should move"
    );
    wheel(&mut cx, 350.)?;
    assert_ne!(
        history.offset(),
        offset,
        "History should scroll outside the menu"
    );
    visual.update(|window, cx| {
        for _ in 0..12 {
            window.dispatch_keystroke(Keystroke::parse("down").unwrap(), cx);
        }
        window.dispatch_keystroke(Keystroke::parse("up").unwrap(), cx);
        window.refresh();
        window.draw(cx).clear(cx);
    });
    window.update(&mut cx, |view, window, cx| {
        assert_eq!(view.app.read(cx).composer_menu.selected, 11);
        assert!(scroll.offset().y < px(-150.));
        assert!(view.app.update(cx, |app, cx| app.choose_composer_menu(
            &view.draft,
            window,
            cx
        )));
        let draft = view.draft.read(cx);
        assert_eq!(draft.value().as_ref(), "/skill-11 ");
        assert_eq!(draft.tokens()[0].token().label().as_ref(), "skill-11");
        assert_eq!(
            draft.tokens()[0].token().id().as_ref(),
            "command:tool-case:skill-11"
        );
    })?;
    Ok(())
}
