use gpui_kit::component::{Theme, ThemeMode, TitleBar};
use gpui_kit::*;
use vespera::{Startup, assets::Assets};

actions!(vespera, [Quit]);

fn main() {
    application().with_assets(Assets).run(|cx| {
        init(cx);
        Theme::change(ThemeMode::Light, None, cx);
        cx.set_app_identity("dev.vespera.desktop", "Vespera");
        cx.on_action(|_: &Quit, cx| cx.quit());
        cx.bind_keys([KeyBinding::new("cmd-q", Quit, None)]);
        let width = cx
            .primary_display()
            .map(|display| (display.visible_bounds().size.width - px(300.)).max(px(1.)))
            .unwrap_or(px(1440.));
        let bounds = Bounds::centered(None, size(width, px(960.)), cx);
        let mut options = TitleBar::window_options();
        options.window_bounds = Some(WindowBounds::Windowed(bounds));
        options.window_min_size = Some(size(px(1280.).min(width), px(800.)));
        options.titlebar = Some(TitlebarOptions {
            title: Some("Vespera".into()),
            appears_transparent: true,
            traffic_light_position: Some(point(px(20.), px(18.))),
        });
        open_window(options, cx, |window, cx| {
            cx.new(|cx| Startup::new(window, cx))
        })
        .expect("Cannot open Vespera window");
        cx.activate(true);
    });
}
