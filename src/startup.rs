use crate::{Vespera, theme::*};
use gpui_kit::base::animation::cubic_bezier;
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
const PLATE_SIZE: f32 = 176.;
const LOGO_SIZE: f32 = PLATE_SIZE * (2. / 3.);

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

        let page = cx.new(Vespera::new);
        let view = page.downgrade();
        App::on_action(cx, move |_: &crate::ReloadChats, cx| {
            let _ = view.update(cx, |this, cx| this.reload_chats(cx));
        });
        Self {
            page,
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
        let reduce_motion = cx.reduce_motion();
        let elapsed = if reduce_motion {
            MOTION
        } else {
            self.started.map_or(Duration::ZERO, |start| start.elapsed())
        };
        if !reduce_motion && self.started.is_some() && elapsed < MOTION + HOLD {
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
    let time = (elapsed.as_secs_f32() - 0.8) / (MOTION.as_secs_f32() - 0.8);
    let progress = cubic_bezier(0.62, 0., 0.23, 1.)(time);
    (0.16 + 0.84 * progress, -TAU * (1. - progress))
}

fn flow_time(elapsed: Duration) -> f32 {
    elapsed.min(MOTION).as_secs_f32()
        + elapsed.saturating_sub(MOTION).min(HOLD).as_secs_f32() * 0.85
}

fn gradient_offset(elapsed: Duration, phase: f32) -> Point<f32> {
    let t = flow_time(elapsed) / MOTION.as_secs_f32();
    let angle = phase + 2.2 * t;
    point(0.24 * angle.cos(), 0.19 * (1.3 * angle + 0.6).sin())
}

fn plate_pose(viewport: Size<Pixels>, elapsed: Duration) -> (Size<Pixels>, Pixels, Pixels) {
    let progress = ease(elapsed.as_secs_f32(), 0.25, MOTION.as_secs_f32());
    let size = viewport.map(|length| length + (px(PLATE_SIZE) - length) * progress);
    let contraction_ripple = size.width.min(size.height) * (0.52 * progress * (1. - progress));
    (
        size,
        px(36.) * progress + contraction_ripple * 0.55,
        contraction_ripple + px(14.) * progress,
    )
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
    let wave = |p: Point<Pixels>| {
        let u = f32::from(p.x / w);
        let v = f32::from(p.y / h);
        (phase + 5. * v + 1.5 * u, phase * 1.4 + 6. * u - 2.5 * v)
    };
    let at = |p: Point<Pixels>| {
        let (x, y) = wave(p);
        plate.origin + p + point(ripple * x.sin(), ripple * y.cos())
    };
    // Warp the tangents at each join so adjacent curves stay smooth.
    let handle = |anchor: Point<Pixels>, control: Point<Pixels>| {
        let delta = control - anchor;
        let du = f32::from(delta.x / w);
        let dv = f32::from(delta.y / h);
        let (x, y) = wave(anchor);
        at(anchor)
            + delta
            + point(
                ripple * x.cos() * (5. * dv + 1.5 * du),
                -ripple * y.sin() * (6. * du - 2.5 * dv),
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
    let start = point(r, zero);
    mask.move_to(at(start));
    let mut previous = start;
    let mut curve = |end, first, second| {
        mask.cubic_bezier_to(at(end), handle(previous, first), handle(end, second));
        previous = end;
    };
    curve(
        point(w - r, zero),
        point(w * 0.33, zero),
        point(w * 0.67, zero),
    );
    curve(point(w, r), point(w - r + k, zero), point(w, r - k));
    curve(point(w, h - r), point(w, h * 0.33), point(w, h * 0.67));
    curve(point(w - r, h), point(w, h - r + k), point(w - r + k, h));
    curve(point(r, h), point(w * 0.67, h), point(w * 0.33, h));
    curve(point(zero, h - r), point(r - k, h), point(zero, h - r + k));
    curve(point(zero, r), point(zero, h * 0.67), point(zero, h * 0.33));
    curve(start, point(zero, r - k), point(r - k, zero));
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
                        let wave_phase = phase + flow_time(elapsed) * 3.;
                        cover_outside_plate(window, viewport, plate, radius, ripple, wave_phase);
                    }
                },
            )
            .absolute()
            .inset_0(),
        )
        .child(
            icon("vespera-trefoil", LOGO_SIZE, WHITE)
                .opacity(ease(elapsed.as_secs_f32(), 0.8, 1.24))
                .with_transformation(
                    Transformation::rotate(radians(logo_angle))
                        .with_scaling(size(logo_scale, logo_scale)),
                ),
        )
}

#[cfg(test)]
mod tests {
    use super::{HOLD, MOTION, PLATE_SIZE, TAU, gradient_offset, logo_pose, plate_pose};
    use gpui_kit::{px, size};
    use std::time::Duration;

    #[test]
    fn trefoil_scales_and_rotates_then_stops_for_the_hold() {
        assert_eq!(logo_pose(Duration::ZERO), (0.16, -TAU));
        assert_eq!(logo_pose(Duration::from_millis(800)), (0.16, -TAU));
        let angle_at = |ms| logo_pose(Duration::from_millis(ms)).1;
        let start = angle_at(1000) - angle_at(800);
        let accelerating = angle_at(1200) - angle_at(1000);
        let peak = angle_at(1400) - angle_at(1200);
        let braking = angle_at(1600) - angle_at(1400);
        let slow = angle_at(1800) - angle_at(1600);
        let stop = angle_at(2000) - angle_at(1800);
        assert!(0. < start && start < accelerating && accelerating < peak);
        assert!(0. < stop && stop < slow && slow < braking && braking < peak);
        assert_eq!(logo_pose(MOTION), (1., 0.));
        assert_eq!(logo_pose(MOTION + HOLD), logo_pose(MOTION));
    }

    #[test]
    fn gradient_moves_with_the_launch_phase_and_continues_during_the_hold() {
        assert_ne!(
            gradient_offset(Duration::ZERO, 0.),
            gradient_offset(Duration::ZERO, 1.)
        );
        assert_ne!(
            gradient_offset(Duration::ZERO, 1.),
            gradient_offset(MOTION, 1.)
        );
        assert_ne!(
            gradient_offset(MOTION, 1.),
            gradient_offset(MOTION + HOLD, 1.)
        );
    }

    #[test]
    fn plate_contracts_but_keeps_an_irregular_outline() {
        let viewport = size(px(1280.), px(800.));
        let end = (size(px(PLATE_SIZE), px(PLATE_SIZE)), px(36.), px(14.));
        assert_eq!(
            plate_pose(viewport, Duration::ZERO),
            (viewport, px(0.), px(0.))
        );
        let (mid, radius, ripple) = plate_pose(viewport, MOTION / 2);
        assert!(mid.width < viewport.width && mid.width > end.0.width);
        assert!(radius > px(0.));
        assert!(ripple > end.2);
        assert_eq!(plate_pose(viewport, MOTION), end);
        assert_eq!(plate_pose(viewport, MOTION + HOLD), end);
    }
}
