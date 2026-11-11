use block2::{DynBlock, RcBlock};
use objc2::{
    ClassType, define_class,
    rc::Retained,
    runtime::{Bool, ProtocolObject},
};
use objc2_foundation::{NSError, NSObject, NSObjectProtocol, NSString};
use objc2_user_notifications::{
    UNAuthorizationOptions, UNMutableNotificationContent, UNNotification,
    UNNotificationPresentationOptions, UNNotificationRequest, UNUserNotificationCenter,
    UNUserNotificationCenterDelegate,
};
use std::{
    cell::OnceCell,
    sync::atomic::{AtomicU64, Ordering},
};

define_class!(
    #[unsafe(super(NSObject))]
    struct ForegroundNotifications;

    unsafe impl NSObjectProtocol for ForegroundNotifications {}

    unsafe impl UNUserNotificationCenterDelegate for ForegroundNotifications {
        #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
        fn present(
            &self,
            _: &UNUserNotificationCenter,
            _: &UNNotification,
            completion: &DynBlock<dyn Fn(UNNotificationPresentationOptions)>,
        ) {
            completion
                .call((UNNotificationPresentationOptions::Banner
                    | UNNotificationPresentationOptions::List,));
        }
    }
);

thread_local! {
    static DELEGATE: OnceCell<Retained<ForegroundNotifications>> = const { OnceCell::new() };
}
static NEXT_ID: AtomicU64 = AtomicU64::new(0);

pub fn notify(title: &str, body: &str) {
    let center = UNUserNotificationCenter::currentNotificationCenter();
    DELEGATE.with(|cell| {
        let delegate =
            cell.get_or_init(|| unsafe { objc2::msg_send![ForegroundNotifications::class(), new] });
        center.setDelegate(Some(ProtocolObject::from_ref(&**delegate)));
    });
    let content = UNMutableNotificationContent::new();
    content.setTitle(&NSString::from_str(title));
    content.setBody(&NSString::from_str(body));
    let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let request = UNNotificationRequest::requestWithIdentifier_content_trigger(
        &NSString::from_str(&format!("vespera-chat-{id}")),
        &content,
        None,
    );
    let deliver = RcBlock::new(move |allowed: Bool, _: *mut NSError| {
        if allowed.as_bool() {
            UNUserNotificationCenter::currentNotificationCenter()
                .addNotificationRequest_withCompletionHandler(&request, None);
        }
    });
    center
        .requestAuthorizationWithOptions_completionHandler(UNAuthorizationOptions::Alert, &deliver);
}
