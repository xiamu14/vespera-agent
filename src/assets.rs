use gpui_kit::{AssetSource, SharedString};
use rust_embed::RustEmbed;
use std::borrow::Cow;

#[derive(RustEmbed)]
#[folder = "assets/"]
struct Embedded;

pub struct Assets;

impl AssetSource for Assets {
    fn load(&self, path: &str) -> anyhow::Result<Option<Cow<'static, [u8]>>> {
        match Embedded::get(path) {
            Some(asset) => Ok(Some(asset.data)),
            None => gpui_kit::assets::Assets.load(path),
        }
    }

    fn list(&self, path: &str) -> anyhow::Result<Vec<SharedString>> {
        let mut paths = gpui_kit::assets::Assets.list(path)?;
        paths.extend(
            Embedded::iter()
                .filter(|name| name.starts_with(path))
                .map(|name| SharedString::from(name.into_owned())),
        );
        Ok(paths)
    }
}
