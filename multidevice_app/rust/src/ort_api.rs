// Prefix of Microsoft's stable OrtApi v1 C ABI through ReleaseSessionOptions.
// Generated from onnxruntime v1.17.3/include/onnxruntime/core/session/onnxruntime_c_api.h.
// Only functions used by the CPU worker are typed. Reserved slots contain function pointers.
#![allow(non_snake_case)]
use std::ffi::{c_char, c_void};
pub type P = *mut c_void;
#[repr(C)]
pub struct Api {
    _unused_0: [P; 2],
    pub GetErrorMessage: unsafe extern "system" fn(P) -> *const c_char,
    pub CreateEnv: unsafe extern "system" fn(i32, *const c_char, *mut P) -> P,
    _unused_4: [P; 4],
    pub CreateSessionFromArray: unsafe extern "system" fn(P, *const c_void, usize, P, *mut P) -> P,
    pub Run: unsafe extern "system" fn(
        P,
        P,
        *const *const c_char,
        *const P,
        usize,
        *const *const c_char,
        usize,
        *mut P,
    ) -> P,
    pub CreateSessionOptions: unsafe extern "system" fn(*mut P) -> P,
    _unused_11: [P; 38],
    pub CreateTensorWithDataAsOrtValue:
        unsafe extern "system" fn(P, P, usize, *const i64, usize, i32, *mut P) -> P,
    _unused_50: [P; 1],
    pub GetTensorMutableData: unsafe extern "system" fn(P, *mut P) -> P,
    _unused_52: [P; 8],
    pub GetTensorElementType: unsafe extern "system" fn(P, *mut i32) -> P,
    pub GetDimensionsCount: unsafe extern "system" fn(P, *mut usize) -> P,
    pub GetDimensions: unsafe extern "system" fn(P, *mut i64, usize) -> P,
    _unused_63: [P; 2],
    pub GetTensorTypeAndShape: unsafe extern "system" fn(P, *mut P) -> P,
    _unused_66: [P; 3],
    pub CreateCpuMemoryInfo: unsafe extern "system" fn(i32, i32, *mut P) -> P,
    _unused_70: [P; 22],
    pub ReleaseEnv: unsafe extern "system" fn(P),
    pub ReleaseStatus: unsafe extern "system" fn(P),
    pub ReleaseMemoryInfo: unsafe extern "system" fn(P),
    pub ReleaseSession: unsafe extern "system" fn(P),
    pub ReleaseValue: unsafe extern "system" fn(P),
    _unused_97: [P; 2],
    pub ReleaseTensorTypeAndShapeInfo: unsafe extern "system" fn(P),
    pub ReleaseSessionOptions: unsafe extern "system" fn(P),
}
