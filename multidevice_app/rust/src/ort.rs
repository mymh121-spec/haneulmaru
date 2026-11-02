use crate::Result;
#[path = "ort_api.rs"]
mod ffi;
use ffi::{Api, P};
use std::{
    ffi::{c_char, c_void, CStr, CString},
    fs, ptr, slice,
};

#[cfg(windows)]
fn symbol() -> Result<P> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    extern "system" {
        fn LoadLibraryExW(name: *const u16, file: P, flags: u32) -> P;
        fn GetProcAddress(module: P, name: *const c_char) -> P;
    }
    // Load from System32 by default. An explicit absolute path can select a newer official runtime.
    let path = std::env::var_os("HANEULMARU_ORT_LIB");
    let (name, flags) = match path {
        Some(p) if std::path::Path::new(&p).is_absolute() => (p, 0x100 | 0x800),
        Some(_) => return Err("HANEULMARU_ORT_LIB must be an absolute path".into()),
        None => ("onnxruntime.dll".into(), 0x800),
    };
    let name: Vec<_> = name.encode_wide().chain(Some(0)).collect();
    unsafe {
        let lib = LoadLibraryExW(name.as_ptr(), ptr::null_mut(), flags);
        if lib.is_null() {
            return Err(format!(
                "ONNX Runtime load failed: {}",
                std::io::Error::last_os_error()
            )
            .into());
        }
        Ok(GetProcAddress(lib, c"OrtGetApiBase".as_ptr()))
    }
}
#[cfg(not(windows))]
fn symbol() -> Result<P> {
    use std::os::unix::ffi::OsStrExt;
    #[link(name = "dl")]
    extern "C" {
        fn dlopen(name: *const c_char, flags: i32) -> P;
        fn dlsym(module: P, name: *const c_char) -> P;
    }
    let path = std::env::var_os("HANEULMARU_ORT_LIB");
    if path
        .as_ref()
        .is_some_and(|p| !std::path::Path::new(p).is_absolute())
    {
        return Err("HANEULMARU_ORT_LIB must be an absolute path".into());
    }
    let name = CString::new(
        path.unwrap_or_else(|| "libonnxruntime.so".into())
            .as_bytes(),
    )?;
    unsafe {
        let lib = dlopen(name.as_ptr(), 2);
        if lib.is_null() {
            return Err("Install ONNX Runtime CPU or set HANEULMARU_ORT_LIB".into());
        }
        Ok(dlsym(lib, c"OrtGetApiBase".as_ptr()))
    }
}

#[repr(C)]
struct ApiBase {
    get_api: unsafe extern "system" fn(u32) -> *const Api,
}
fn api() -> Result<&'static Api> {
    let address = symbol()?;
    if address.is_null() {
        return Err("ONNX Runtime has no OrtGetApiBase export".into());
    }
    // The library stays loaded for the lifetime of this worker.
    unsafe {
        let get_base: unsafe extern "system" fn() -> *const ApiBase = std::mem::transmute(address);
        let base = get_base().as_ref().ok_or("No ONNX Runtime API")?;
        (base.get_api)(1)
            .as_ref()
            .ok_or_else(|| "Unsupported ONNX Runtime".into())
    }
}
struct Handle {
    raw: P,
    release: unsafe extern "system" fn(P),
}
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe { (self.release)(self.raw) }
    }
}
impl Api {
    fn check(&self, status: P) -> Result<()> {
        if status.is_null() {
            return Ok(());
        }
        unsafe {
            let msg = CStr::from_ptr((self.GetErrorMessage)(status))
                .to_string_lossy()
                .into_owned();
            (self.ReleaseStatus)(status);
            Err(msg.into())
        }
    }
    fn handle(
        &self,
        release: unsafe extern "system" fn(P),
        create: impl FnOnce(*mut P) -> P,
    ) -> Result<Handle> {
        let mut raw = ptr::null_mut();
        self.check(create(&mut raw))?;
        if raw.is_null() {
            return Err("Missing ONNX Runtime handle".into());
        }
        Ok(Handle { raw, release })
    }
}

pub struct Session {
    session: Handle,
    mem: Handle,
    _opts: Handle,
    _env: Handle,
    api: &'static Api,
    input: CString,
    output: CString,
}
impl Session {
    pub fn load(path: &str, input: &str, output: &str) -> Result<Self> {
        let data = fs::read(path)?;
        let a = api()?;
        unsafe {
            let env = a.handle(a.ReleaseEnv, |p| {
                (a.CreateEnv)(3, c"haneulmaru".as_ptr(), p)
            })?;
            let opts = a.handle(a.ReleaseSessionOptions, |p| (a.CreateSessionOptions)(p))?;
            let session = a.handle(a.ReleaseSession, |p| {
                (a.CreateSessionFromArray)(env.raw, data.as_ptr().cast(), data.len(), opts.raw, p)
            })?;
            let mem = a.handle(a.ReleaseMemoryInfo, |p| (a.CreateCpuMemoryInfo)(1, 0, p))?;
            Ok(Self {
                session,
                mem,
                _opts: opts,
                _env: env,
                api: a,
                input: CString::new(input)?,
                output: CString::new(output)?,
            })
        }
    }
    pub fn run(&self, input: &mut [f32], size: usize) -> Result<([usize; 2], Vec<f32>)> {
        let a = self.api;
        if size.checked_mul(size).and_then(|n| n.checked_mul(3)) != Some(input.len()) {
            return Err("Invalid input tensor length".into());
        }
        let dims = [1, 3, size as i64, size as i64];
        unsafe {
            // ORT borrows input until the synchronous Run returns.
            let tensor = a.handle(a.ReleaseValue, |p| {
                (a.CreateTensorWithDataAsOrtValue)(
                    self.mem.raw,
                    input.as_mut_ptr().cast(),
                    std::mem::size_of_val(input),
                    dims.as_ptr(),
                    4,
                    1,
                    p,
                )
            })?;
            let out = a.handle(a.ReleaseValue, |p| {
                (a.Run)(
                    self.session.raw,
                    ptr::null_mut(),
                    &self.input.as_ptr(),
                    &tensor.raw,
                    1,
                    &self.output.as_ptr(),
                    1,
                    p,
                )
            })?;
            let shape = a.handle(a.ReleaseTensorTypeAndShapeInfo, |p| {
                (a.GetTensorTypeAndShape)(out.raw, p)
            })?;
            let (mut dtype, mut rank) = (0, 0);
            a.check((a.GetTensorElementType)(shape.raw, &mut dtype))?;
            a.check((a.GetDimensionsCount)(shape.raw, &mut rank))?;
            if dtype != 1 || rank != 3 {
                return Err("Expected a rank-3 float32 detector output".into());
            }
            let mut dims = [0i64; 3];
            a.check((a.GetDimensions)(shape.raw, dims.as_mut_ptr(), rank))?;
            if dims[0] != 1 || dims[1] < 0 || dims[2] < 0 {
                return Err("Expected detector output batch=1".into());
            }
            let shape = [usize::try_from(dims[1])?, usize::try_from(dims[2])?];
            let count = shape[0]
                .checked_mul(shape[1])
                .filter(|n| *n <= 64 * 1024 * 1024)
                .ok_or("Detector output too large")?;
            if count == 0 {
                return Ok((shape, Vec::new()));
            }
            let mut data: *mut c_void = ptr::null_mut();
            a.check((a.GetTensorMutableData)(out.raw, &mut data))?;
            if data.is_null() {
                return Err("Empty ONNX result".into());
            }
            Ok((
                shape,
                slice::from_raw_parts(data.cast::<f32>(), count).to_vec(),
            ))
        }
    }
}
