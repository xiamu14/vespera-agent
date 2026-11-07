use crate::{Vespera, theme::*};
use gpui_kit::*;
use std::f32::consts::TAU;
use std::hash::{BuildHasher, RandomState};
use std::time::{Duration, Instant};

#[cfg(target_os = "macos")]
use objc2::{AnyThread, rc::Retained};
#[cfg(target_os = "macos")]
use objc2_app_kit::NSSound;
#[cfg(target_os = "macos")]
use objc2_foundation::NSData;

const MOTION: Duration = Duration::from_secs(2);
const HOLD: Duration = Duration::from_secs(1);
const WHITE: u32 = 0xffffff;
const GRADIENT_EDGE: u32 = 0xff9333;
const LOGO_SIZE: f32 = 176.;

pub struct Startup {
    page: Entity<Vespera>,
    started: Option<Instant>,
    gradient_phase: f32,
    finished: bool,
    #[cfg(target_os = "macos")]
    sound: Option<Retained<NSSound>>,
}

impl Startup {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        cx.on_next_frame(window, |this, _, cx| {
            #[cfg(target_os = "macos")]
            {
                let data = NSData::with_bytes(include_bytes!("../assets/startup.wav"));
                this.sound = NSSound::initWithData(NSSound::alloc(), &data);
                if !this.sound.as_ref().is_some_and(|sound| sound.play()) {
                    eprintln!("Vespera could not play the startup sound");
                }
            }
            this.started = Some(Instant::now());
            cx.notify();

            let timer = cx.background_executor().timer(MOTION + HOLD);
            cx.spawn(async move |view, cx| {
                timer.await;
                let _ = view.update(cx, |this, cx| {
                    this.finished = true;
                    this.stop_sound();
                    cx.notify();
                });
            })
            .detach();
        });

        Self {
            page: cx.new(|_| Vespera::default()),
            started: None,
            gradient_phase: RandomState::new().hash_one(0_u8) as f32 / u64::MAX as f32 * TAU,
            finished: false,
            #[cfg(target_os = "macos")]
            sound: None,
        }
    }

    fn stop_sound(&mut self) {
        #[cfg(target_os = "macos")]
        if let Some(sound) = self.sound.take() {
            sound.stop();
        }
    }
}

impl Drop for Startup {
    fn drop(&mut self) {
        self.stop_sound();
    }
}

impl Render for Startup {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if self.finished {
            return self.page.clone().into_any_element();
        }
        let elapsed = if cx.reduce_motion() {
            MOTION
        } else {
            self.started.map_or(Duration::ZERO, |start| start.elapsed())
        };
        if self.started.is_some() && elapsed < MOTION {
            window.request_animation_frame();
        }
        splash(elapsed, self.gradient_phase).into_any_element()
    }
}

fn ease(time: f32, start: f32, end: f32) -> f32 {
    let t = ((time - start) / (end - start)).clamp(0., 1.);
    t * t * (3. - 2. * t)
}

fn logo_pose(elapsed: Duration) -> (f32, f32) {
    let progress = ease(elapsed.as_secs_f32(), 0., MOTION.as_secs_f32());
    (0.16 + 0.84 * progress, -TAU * (1. - progress))
}

fn gradient_offset(elapsed: Duration, phase: f32) -> Point<f32> {
    let t = elapsed.min(MOTION).as_secs_f32() / MOTION.as_secs_f32();
    let angle = phase + 2.2 * t;
    point(0.24 * angle.cos(), 0.19 * (1.3 * angle + 0.6).sin())
}

fn plate_pose(viewport: Size<Pixels>, elapsed: Duration) -> (Size<Pixels>, Pixels, Pixels) {
    let progress = ease(elapsed.as_secs_f32(), 0.25, MOTION.as_secs_f32());
    let size = viewport.map(|length| length + (px(LOGO_SIZE) - length) * progress);
    let ripple = size.width.min(size.height) * (0.64 * progress * (1. - progress));
    (size, px(36.) * progress + ripple * 0.55, ripple)
}

fn cover_outside_plate(
    window: &mut Window,
    viewport: Bounds<Pixels>,
    plate: Bounds<Pixels>,
    radius: Pixels,
    ripple: Pixels,
    phase: f32,
) {
    let w = plate.size.width;
    let h = plate.size.height;
    let r = radius;
    let k = r * 0.552_284_8;
    let at = |x: Pixels, y: Pixels| {
        let u = f32::from(x / w);
        let v = f32::from(y / h);
        plate.origin
            + point(
                x + ripple * (phase + 7. * v + 2. * u).sin(),
                y + ripple * (phase * 1.4 + 8. * u - 3. * v).cos(),
            )
    };
    let zero = px(0.);
    let mut mask = PathBuilder::fill();
    mask.add_polygon(
        &[
            viewport.origin,
            point(viewport.right(), viewport.top()),
            point(viewport.right(), viewport.bottom()),
            point(viewport.left(), viewport.bottom()),
        ],
        true,
    );
    mask.move_to(at(r, zero));
    mask.cubic_bezier_to(at(w - r, zero), at(w * 0.33, zero), at(w * 0.67, zero));
    mask.cubic_bezier_to(at(w, r), at(w - r + k, zero), at(w, r - k));
    mask.cubic_bezier_to(at(w, h - r), at(w, h * 0.33), at(w, h * 0.67));
    mask.cubic_bezier_to(at(w - r, h), at(w, h - r + k), at(w - r + k, h));
    mask.cubic_bezier_to(at(r, h), at(w * 0.67, h), at(w * 0.33, h));
    mask.cubic_bezier_to(at(zero, h - r), at(r - k, h), at(zero, h - r + k));
    mask.cubic_bezier_to(at(zero, r), at(zero, h * 0.67), at(zero, h * 0.33));
    mask.cubic_bezier_to(at(r, zero), at(zero, r - k), at(r - k, zero));
    mask.close();
    if let Ok(path) = mask.build() {
        window.paint_path(path, rgb(WHITE));
    }
}

fn splash(elapsed: Duration, phase: f32) -> Div {
    let (logo_scale, logo_angle) = logo_pose(elapsed);
    let offset = gradient_offset(elapsed, phase);

    column()
        .relative()
        .size_full()
        .overflow_hidden()
        .bg(rgb(WHITE))
        .items_center()
        .justify_center()
        .child(
            canvas(
                |_, _, _| (),
                move |viewport, (), window, cx| {
                    let (plate_size, radius, ripple) = plate_pose(viewport.size, elapsed);
                    let plate = Bounds {
                        origin: viewport.center()
                            - point(plate_size.width * 0.5, plate_size.height * 0.5),
                        size: plate_size,
                    };
                    let (paint_bounds, radii) = if ripple > px(0.) {
                        (viewport, Corners::all(px(0.)))
                    } else {
                        (plate, Corners::all(radius))
                    };
                    let mut background = fill(paint_bounds, rgb(GRADIENT_EDGE));
                    background.corner_radii = radii;
                    window.paint_quad(background);

                    let source = Resource::Embedded("startup-gradient.png".into());
                    if let Some(Ok(image)) = window.use_asset::<ImgResourceLoader>(&source, cx) {
                        let side = plate_size.width.max(plate_size.height) * 1.55;
                        let image_bounds = Bounds {
                            origin: plate.center()
                                + point(plate_size.width * offset.x, plate_size.height * offset.y)
                                - point(side * 0.5, side * 0.5),
                            size: size(side, side),
                        };
                        if let Err(error) =
                            window.paint_image(paint_bounds, image_bounds, radii, image, 0, false)
                        {
                            eprintln!("Vespera could not paint the startup gradient: {error}");
                        }
                    }
                    if ripple > px(0.) {
                        let wave_phase = phase + elapsed.as_secs_f32() * 3.;
                        cover_outside_plate(window, viewport, plate, radius, ripple, wave_phase);
                    }
                },
            )
            .absolute()
            .inset_0(),
        )
        .child(
            icon("vespera-trefoil", LOGO_SIZE, WHITE).with_transformation(
                Transformation::rotate(radians(logo_angle))
                    .with_scaling(size(logo_scale, logo_scale)),
            ),
        )
}

#[cfg(test)]
mod tests {
    use super::{HOLD, LOGO_SIZE, MOTION, TAU, gradient_offset, logo_pose, plate_pose};
    use gpui_kit::{px, size};
    use std::time::Duration;

    #[test]
    fn trefoil_scales_and_rotates_then_stops_for_the_hold() {
        assert_eq!(logo_pose(Duration::ZERO), (0.16, -TAU));
        let (scale, angle) = logo_pose(MOTION / 2);
        assert!((scale - 0.58).abs() < f32::EPSILON);
        assert_eq!(angle, -TAU / 2.);
        assert_eq!(logo_pose(MOTION), (1., 0.));
        assert_eq!(logo_pose(MOTION + HOLD), logo_pose(MOTION));
    }

    #[test]
    fn gradient_moves_with_the_launch_phase_and_stops_for_the_hold() {
        assert_ne!(
            gradient_offset(Duration::ZERO, 0.),
            gradient_offset(Duration::ZERO, 1.)
        );
        assert_ne!(
            gradient_offset(Duration::ZERO, 1.),
            gradient_offset(MOTION, 1.)
        );
        assert_eq!(
            gradient_offset(MOTION, 1.),
            gradient_offset(MOTION + HOLD, 1.)
        );
    }

    #[test]
    fn plate_contracts_from_the_viewport_to_a_rounded_square() {
        let viewport = size(px(1280.), px(800.));
        let end = (size(px(LOGO_SIZE), px(LOGO_SIZE)), px(36.), px(0.));
        assert_eq!(
            plate_pose(viewport, Duration::ZERO),
            (viewport, px(0.), px(0.))
        );
        let (mid, radius, ripple) = plate_pose(viewport, MOTION / 2);
        assert!(mid.width < viewport.width && mid.width > end.0.width);
        assert!(radius > px(0.));
        assert!(ripple > px(0.));
        assert_eq!(plate_pose(viewport, MOTION), end);
        assert_eq!(plate_pose(viewport, MOTION + HOLD), end);
    }
}
