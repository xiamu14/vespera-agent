//! In-process transport for the bundled core. Clients own scheduling and presentation.
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::os::unix::ffi::OsStrExt;
use std::path::Path;
use std::sync::OnceLock;

use anyhow::{Result, anyhow, ensure};

type Initialize =
    unsafe extern "C" fn(*const c_char, *const c_char, *const c_char, *const c_char) -> c_int;
type Request = unsafe extern "C" fn(*const c_char) -> *mut c_char;
type Free = unsafe extern "C" fn(*mut c_char);

unsafe extern "C" {
    fn dlopen(path: *const c_char, mode: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, name: *const c_char) -> *mut c_void;
    fn dlerror() -> *const c_char;
}

pub struct AgentBridge {
    request: Request,
    free: Free,
}

static BUNDLED: OnceLock<Result<AgentBridge, String>> = OnceLock::new();

impl AgentBridge {
    /// Returns the process-wide bundled core. Call from a worker thread.
    pub fn bundled() -> Result<&'static Self> {
        BUNDLED
            .get_or_init(|| Self::open_application().map_err(|error| error.to_string()))
            .as_ref()
            .map_err(|error| anyhow!("{error}"))
    }

    fn open_application() -> Result<Self> {
        let executable = std::env::current_exe()?;
        let contents = executable
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| anyhow!("Application bundle is missing"))?;
        let home = std::env::var_os("HOME").ok_or_else(|| anyhow!("HOME is missing"))?;
        let data = Path::new(&home).join("Library/Application Support/Vespera");
        Self::open(contents, &data)
    }

    pub fn is_bundled() -> bool {
        std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .is_some_and(|path| path.ends_with("Contents/MacOS"))
    }

    pub fn start_bundled() {
        if Self::is_bundled() {
            std::thread::spawn(|| {
                if let Err(error) = Self::bundled() {
                    eprintln!("Vespera Agent Core: {error}");
                }
            });
        }
    }

    /// Loads the bridge from an application bundle. Call from a worker thread.
    pub fn open(contents: &Path, data: &Path) -> Result<Self> {
        let resources = contents.join("Resources");
        let path = |path: &Path| CString::new(path.as_os_str().as_bytes());
        let library = path(&contents.join("Frameworks/libvespera_agent.dylib"))?;
        let home = path(&resources.join("python"))?;
        let core = path(&resources.join("agent_core"))?;
        let source = path(&resources.join("coder-cli"))?;
        let data = path(data)?;
        // CPython and its extension modules remain loaded for the lifetime of the host.
        let handle = unsafe { dlopen(library.as_ptr(), 2 | 8) }; // RTLD_NOW | RTLD_GLOBAL on macOS.
        if handle.is_null() {
            let error = unsafe { dlerror() };
            ensure!(!error.is_null(), "Could not load the Vespera Agent Core");
            return Err(anyhow!(
                unsafe { CStr::from_ptr(error) }
                    .to_string_lossy()
                    .into_owned()
            ));
        }
        let symbol = |name: &CStr| {
            let address = unsafe { dlsym(handle, name.as_ptr()) };
            ensure!(!address.is_null(), "Missing Agent Core symbol {name:?}");
            Ok(address)
        };
        let initialize: Initialize =
            unsafe { std::mem::transmute(symbol(c"vespera_agent_initialize")?) };
        let request: Request = unsafe { std::mem::transmute(symbol(c"vespera_agent_request")?) };
        let free: Free = unsafe { std::mem::transmute(symbol(c"vespera_agent_free")?) };
        let status =
            unsafe { initialize(home.as_ptr(), core.as_ptr(), source.as_ptr(), data.as_ptr()) };
        ensure!(status == 0, "Could not initialize the bundled Agent Core");
        Ok(Self { request, free })
    }

    /// Sends a JSON command to the core. A send command starts a run, and poll retrieves events.
    pub fn request(&self, json: &str) -> Result<String> {
        let json = CString::new(json)?;
        let reply = unsafe { (self.request)(json.as_ptr()) };
        ensure!(!reply.is_null(), "The Agent Core request failed");
        let result = unsafe { CStr::from_ptr(reply) }.to_str().map(str::to_owned);
        unsafe { (self.free)(reply) };
        Ok(result?)
    }
}
