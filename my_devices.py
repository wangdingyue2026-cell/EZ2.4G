#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外设电量 —?Windows 10/11 无线外设电量托盘工具

纯标准库 + ctypes + tkinter，零第三方依赖。

用法：
    python my_devices.py              # 托盘 + 电量面板
    python my_devices.py --once       # 读一次并打印
    python my_devices.py --list       # 列出的 HID 接口
    python my_devices.py --panel      # 只开面板（不常驻托盘）
    python my_devices.py --install    # 创建开机自启 + 桌面入口
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import ctypes.wintypes as w
import dataclasses
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import queue
import random
import threading
import time
from typing import Optional

UI_SCALE = 1.0


# ===========================================================================
# Win32 基础
# ===========================================================================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)

INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                         ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.ResetEvent.argtypes = [wt.HANDLE]
user32.SetTimer.argtypes = [wt.HWND, wt.UINT, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, wt.UINT]
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.SetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_size_t]
user32.SetClassLongPtrW.restype = ctypes.c_size_t
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str = ""):
        super().__init__()
        self.parse(s or "{00000000-0000-0000-0000-000000000000}")

    def parse(self, s: str):
        s = s.strip().strip("{}")
        parts = s.split("-", 3)
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        rest = bytes.fromhex(parts[3].replace("-", ""))
        for i in range(8):
            self.Data4[i] = rest[i]

    def as_str(self) -> str:
        return ("{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}"
                % (self.Data1, self.Data2, self.Data3,
                   self.Data4[0], self.Data4[1], self.Data4[2], self.Data4[3],
                   self.Data4[4], self.Data4[5], self.Data4[6], self.Data4[7]))


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


class HIDP_RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class HIDP_NOTRANGE(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("Reserved1", wt.USHORT),
                ("StringIndex", wt.USHORT), ("Reserved2", wt.USHORT),
                ("DesignatorIndex", wt.USHORT), ("Reserved3", wt.USHORT),
                ("DataIndex", wt.USHORT), ("Reserved4", wt.USHORT)]


class HIDP_UNION(ctypes.Union):
    _fields_ = [("Range", HIDP_RANGE), ("NotRange", HIDP_NOTRANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", wt.USHORT),
        ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
        ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT),
        ("ReportCount", wt.USHORT), ("Reserved2", wt.USHORT * 5),
        ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
        ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG),
        ("PhysicalMin", wt.LONG), ("PhysicalMax", wt.LONG),
        ("u", HIDP_UNION),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), wt.LPWSTR, wt.DWORD,
    ctypes.POINTER(wt.DWORD)]

hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetOutputReport.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wt.USHORT), ctypes.c_void_p]
hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT,
                                   ctypes.POINTER(wt.ULONG), ctypes.c_void_p,
                                   ctypes.POINTER(HIDP_VALUE_CAPS), ctypes.c_void_p]

GUID_DEVINTERFACE_HID = GUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

HIDP_INPUT = 0
HIDP_OUTPUT = 1
HIDP_FEATURE = 2

# ===========================================================================
# HID 枚举与读写
# ===========================================================================


@dataclasses.dataclass
class HidIface:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product: str = ""
    manufacturer: str = ""
    instance: str = ""

    @property
    def label(self) -> str:
        return (f"{self.vid:04X}:{self.pid:04X} "
                f"UP={self.usage_page:04X} U={self.usage:04X} "
                f"in={self.input_len} out={self.output_len} feat={self.feature_len}")


def _open_path(path: str, access: int, overlapped: bool = False) -> int:
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    return kernel32.CreateFileW(path, access, FILE_SHARE_RW, None,
                                OPEN_EXISTING, flags, None)


def enumerate_hid() -> list[HidIface]:
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_HID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or not hdev:
        return []
    out: list[HidIface] = []
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            idx += 1
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, need.value,
                    ctypes.byref(need), None):
                continue
            # DevicePath 在 cbSize(DWORD) 之后：偏移 4
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            inst = ctypes.create_unicode_buffer(512)
            inst_len = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInstanceIdW(
                hdev, ctypes.byref(did), inst, 512, ctypes.byref(inst_len))

            h = _open_path(path, 0)
            if not h or h == INVALID_HANDLE_VALUE:
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                hid.HidD_GetAttributes(h, ctypes.byref(attrs))
                caps = HIDP_CAPS()
                pp = ctypes.c_void_p()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                prod = ctypes.create_unicode_buffer(256)
                hid.HidD_GetProductString(h, prod, 512)
                manu = ctypes.create_unicode_buffer(256)
                hid.HidD_GetManufacturerString(h, manu, 512)
                out.append(HidIface(
                    path=path, vid=attrs.VendorID, pid=attrs.ProductID,
                    usage_page=caps.UsagePage, usage=caps.Usage,
                    input_len=caps.InputReportByteLength,
                    output_len=caps.OutputReportByteLength,
                    feature_len=caps.FeatureReportByteLength,
                    product=prod.value, manufacturer=manu.value,
                    instance=inst.value))
            finally:
                kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


class HidError(Exception):
    pass


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD),
                ("hEvent", wt.HANDLE)]


class HidDevice:
    """打开一个 HID 接口。
    读句柄始终单独以 FILE_FLAG_OVERLAPPED 打开：绝不在同一句柄上并发阻塞
    ReadFile 与 WriteFile/IOCTL（实测会直接卡死进程）。
    """

    def __init__(self, path: str, write: bool = False):
        self.path = path
        self.write_mode = write
        self.h = 0
        self.rh = 0
        access = GENERIC_READ | (GENERIC_WRITE if write else 0)
        self.h = _open_path(path, access)
        if not self.h or self.h == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            if write:
                self.h = _open_path(path, 0)
            if not self.h or self.h == INVALID_HANDLE_VALUE:
                raise HidError(f"打开接口失败（错误码 {err}）")
            self.write_mode = False
        self.rh = _open_path(path, GENERIC_READ, overlapped=True)
        if not self.rh or self.rh == INVALID_HANDLE_VALUE:
            self.rh = 0

    def close(self):
        for h in (self.rh, self.h):
            if h and h != INVALID_HANDLE_VALUE:
                kernel32.CloseHandle(h)
        self.rh = 0
        self.h = 0

    def write_output(self, data: bytes) -> int:
        buf = ctypes.create_string_buffer(data, len(data))
        written = wt.DWORD(0)
        ok = kernel32.WriteFile(self.h, buf, len(data),
                                ctypes.byref(written), None)
        if not ok:
            raise HidError(f"写入失败（错误码 {ctypes.get_last_error()}）")
        return written.value

    def read_input(self, length: int, timeout_ms: int = 800) -> Optional[bytes]:
        if not self.rh:
            return None
        ev = kernel32.CreateEventW(None, True, False, None)
        if not ev:
            return None
        try:
            ov = OVERLAPPED()
            ov.hEvent = ev
            buf = ctypes.create_string_buffer(length)
            got = wt.DWORD(0)
            ok = kernel32.ReadFile(self.rh, buf, length,
                                   ctypes.byref(got), ctypes.byref(ov))
            if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                return None
            if kernel32.WaitForSingleObject(ev, timeout_ms) == WAIT_TIMEOUT:
                kernel32.CancelIoEx(self.rh, ctypes.byref(ov))
                return None
            kernel32.GetOverlappedResult(self.rh, ctypes.byref(ov),
                                         ctypes.byref(got), False)
            return bytes(buf.raw[:got.value])
        finally:
            kernel32.CloseHandle(ev)

    def get_feature(self, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        if hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def get_feature_report(self, report_id: int, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))[0] = report_id & 0xFF
        if hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def set_feature(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(hid.HidD_SetFeature(self.h, buf, len(data)))

    def get_input_report(self, report_id: int, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))[0] = report_id & 0xFF
        if hid.HidD_GetInputReport(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def set_output_report(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(hid.HidD_SetOutputReport(self.h, buf, len(data)))

    def drain(self):
        while self.rh and self.read_input(256, 15):
            pass

    def value_caps(self, kind: int) -> list[HIDP_VALUE_CAPS]:
        pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return []
        try:
            n = wt.USHORT(0)
            hid.HidP_GetValueCaps(kind, None, ctypes.byref(n), pp)
            if not n.value:
                return []
            arr = (HIDP_VALUE_CAPS * n.value)()
            if not hid.HidP_GetValueCaps(kind, arr, ctypes.byref(n), pp):
                return []
            return list(arr[:n.value])
        finally:
            hid.HidD_FreePreparsedData(pp)

    def usage_value(self, kind: int, report: bytes,
                    cap: HIDP_VALUE_CAPS) -> Optional[int]:
        pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return None
        try:
            val = wt.ULONG(0)
            buf = ctypes.create_string_buffer(report, len(report))
            usage = (cap.u.NotRange.Usage if not cap.IsRange
                     else cap.u.Range.UsageMin)
            ok = hid.HidP_GetUsageValue(kind, cap.UsagePage, cap.LinkCollection,
                                        usage, ctypes.byref(val),
                                        pp, ctypes.byref(cap), buf)
            return val.value if ok else None
        finally:
            hid.HidD_FreePreparsedData(pp)


# ===========================================================================
# 读取结果
# ===========================================================================
@dataclasses.dataclass
class Reading:
    ok: bool = False
    level: Optional[int] = None
    status: str = "unknown"
    source: str = ""
    detail: str = ""
    name: str = ""
    raw: bytes = b""
    ts: float = dataclasses.field(default_factory=time.time)
    present: bool = True     # 设备接口是否存在（False = 真的没插）
    stale: bool = False      # True = 本次没读到，用的是上次成功值
    vid: int = 0
    pid: int = 0
    usage_page: int = 0
    hid_known: bool = False

    @property
    def text(self) -> str:
        return f"{self.level}%" if self.ok and self.level is not None else "未连接"


STATUS_TEXT = {"charging": "充电中", "discharging": "放电中",
               "full": "已充满", "wired": "有线模式", "unknown": "未知"}


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(v)))


# ===========================================================================
# 标准 HID 电量
# ===========================================================================
def read_standard_battery(iface: HidIface) -> Optional[Reading]:
    if not iface.feature_len:
        return None
    dev = HidDevice(iface.path, write=False)
    try:
        for cap in dev.value_caps(HIDP_FEATURE):
            usage = cap.u.NotRange.Usage if not cap.IsRange else cap.u.Range.UsageMin
            if (cap.UsagePage, usage) not in ((0x0006, 0x0020), (0x000D, 0x003B),
                                              (0x0085, 0x0065)):
                continue
            rep = dev.get_feature_report(cap.ReportID, iface.feature_len)
            if not rep:
                continue
            val = dev.usage_value(HIDP_FEATURE, rep, cap)
            if val is None:
                continue
            return Reading(ok=True, level=_clamp(val), source="标准 HID 电量字段",
                           detail=f"UsagePage=0x{cap.UsagePage:04X} "
                                  f"Usage=0x{usage:04X} 原={val}", raw=rep)
    except HidError as e:
        return Reading(ok=False, source="标准 HID 电量字段", detail=str(e))
    finally:
        dev.close()
    return None


# ===========================================================================
# 设备服务
# ===========================================================================
def device_key(vid: int, pid: int) -> str:
    return f"x{vid:04X}{pid:04X}"


def has_device_protocol(vid: int, pid: int) -> bool:
    """只有保存过协议（标准 HID 或抓包）才算有协议。"""
    return f"{vid:04X}:{pid:04X}" in user_protocols()


def save_standard_protocol(vid: int, pid: int) -> None:
    key = f"{vid:04X}:{pid:04X}"
    cfg = load_config()
    protos = cfg.get("protocols")
    if not isinstance(protos, dict):
        protos = {}
    if key not in protos:
        protos[key] = {"kind": "standard"}
        cfg["protocols"] = protos
        save_config(cfg)


def load_saved_devices() -> list:
    raw = load_config().get("devices")
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        try:
            vid = int(d["vid"])
            pid = int(d["pid"])
        except Exception:
            continue
        out.append({"vid": vid, "pid": pid, "name": str(d.get("name", ""))})
    return out


def save_saved_devices(devs: list) -> None:
    cfg = load_config()
    cfg["devices"] = devs
    save_config(cfg)


def read_saved_device(dev: dict, ifaces=None) -> Reading:
    vid = int(dev["vid"])
    pid = int(dev["pid"])
    name = dev.get("name") or ""
    ifaces = ifaces if ifaces is not None else enumerate_hid()
    cand = [d for d in ifaces if d.vid == vid and d.pid == pid]
    if not cand:
        return Reading(ok=False, present=False,
                       name=name or f"{vid:04X}:{pid:04X}",
                       status=("unplugged" if has_device_protocol(vid, pid)
                               else "unknown_proto"),
                       vid=vid, pid=pid, detail="未连接")
    prod = next((d.product for d in cand if d.product), "")
    if not name:
        name = prod or f"{vid:04X}:{pid:04X}"
    # 只允许两条路：标准 HID 直接读，或抓包保存的协议。
    # 不再做厂商兼容试探，也不再按 VID/PID 匹配内置设备。
    r = None
    for d in cand:
        try:
            rr = read_standard_battery(d)
        except Exception:
            rr = None
        if rr and rr.ok and rr.level is not None:
            r = rr
            break
    key = f"{vid:04X}:{pid:04X}"
    proto = user_protocols().get(key)
    if not r or not r.ok:
        if proto is not None and proto.get("kind", "captured") != "standard":
            try:
                rr = _read_user_protocol(cand, proto)
                if rr and rr.ok:
                    r = rr
            except Exception:
                pass
    elif proto is None:
        # 标准 HID 能直接读：保存成协议，之后休眠也算“有协议”
        save_standard_protocol(vid, pid)
    supported = key in user_protocols()
    if r is None:
        r = Reading(
            ok=False, present=True, name=name,
            status="asleep" if supported else "unknown_proto",
            detail=("设备休眠" if supported
                    else "协议未知，无法读取电量"))
    elif not r.ok and supported:
        r.status = "asleep"
        r.source = ("标准 HID 电量"
                    if proto is not None and proto.get("kind") == "standard"
                    else "抓包协议")
        r.detail = "设备休眠"
    r.name = name
    r.present = True
    meta = next((d for d in cand if d.usage_page >= 0xFF00), cand[0])
    r.vid, r.pid = vid, pid
    r.usage_page = meta.usage_page
    r.hid_known = True
    return r


# ---------------------------------------------------------------------------
# 上次成功读数缓存：设备休眠/关机读不到时沿用，只有真正没插才显示未连接
# ---------------------------------------------------------------------------
def _last_store() -> dict:
    cfg = load_config()
    last = cfg.get("last")
    return last if isinstance(last, dict) else {}


def _save_last(last: dict) -> None:
    cfg = load_config()
    cfg["last"] = last
    save_config(cfg)


def forget_last(vid: int, pid: int) -> None:
    cfg = load_config()
    last = cfg.get("last")
    if isinstance(last, dict):
        last.pop(device_key(vid, pid), None)
        save_config(cfg)


def apply_last_known(res: dict[str, Reading]) -> dict[str, Reading]:
    last = _last_store()
    changed = False
    out: dict[str, Reading] = {}
    for key, r in res.items():
        if r.ok and r.level is not None:
            last[key] = {"level": r.level, "ts": r.ts, "name": r.name,
                         "source": r.source, "status": r.status,
                         "vid": r.vid, "pid": r.pid,
                         "usage_page": r.usage_page,
                         "hid_known": r.hid_known}
            changed = True
            out[key] = r
            continue
        if ((not r.ok) and key in last
                and r.status not in ("unknown_proto", "unplugged")):
            item = last[key]
            try:
                level = int(item.get("level"))
            except Exception:
                level = None
            if level is not None:
                ts = float(item.get("ts", time.time()))
                stamp = time.strftime("%H:%M", time.localtime(ts))
                out[key] = Reading(
                    ok=True, level=level,
                    status=item.get("status", "unknown"),
                    source=item.get("source", ""),
                    name=r.name or item.get("name", ""),
                    ts=ts, stale=True,
                    detail=f"上次更新 {stamp} · {r.detail}")
                out[key].vid = int(item.get("vid", 0) or 0)
                out[key].pid = int(item.get("pid", 0) or 0)
                out[key].usage_page = int(item.get("usage_page", 0) or 0)
                out[key].hid_known = bool(item.get("hid_known", False))
                continue
        out[key] = r
    if changed:
        _save_last(last)
    return out


# ===========================================================================
# 主题 / 注册表
# ===========================================================================
def reg_dword(path: str, name: str, default: int = 0) -> int:
    advapi32.RegGetValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR,
                                      wt.DWORD, ctypes.POINTER(wt.DWORD),
                                      ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
    advapi32.RegGetValueW.restype = wt.LONG
    val = wt.DWORD(0)
    size = wt.DWORD(4)
    try:
        rc = advapi32.RegGetValueW(
            ctypes.c_void_p(0x80000001),  # HKEY_CURRENT_USER
            path, name, 0x00000010, None,
            ctypes.byref(val), ctypes.byref(size))
        if rc == 0:
            return val.value
    except Exception:
        pass
    return default


def is_light_theme() -> bool:
    return reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                     "AppsUseLightTheme", 1) != 0


# ===========================================================================
# 图标（GDI 画电量圆环，无需 Pillow）
# ===========================================================================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP)]


gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE,
                                   wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.CreatePen.restype = wt.HPEN
gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int]
gdi32.Arc.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int]
gdi32.Rectangle.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int]
gdi32.Polygon.argtypes = [wt.HDC, ctypes.POINTER(wt.POINT), ctypes.c_int]
gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wt.COLORREF
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]

user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wt.HRGN
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
gdi32.GdiFlush.argtypes = []
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP

COLORREF = lambda r, g, b: (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


def _level_color(level: Optional[int]) -> tuple[int, int, int]:
    if level is None:
        return (120, 128, 140)
    if level >= 60:
        return (34, 197, 94)
    if level >= 30:
        return (245, 158, 11)
    return (239, 68, 68)


class _LogBrush(ctypes.Structure):
    _fields_ = [("lbStyle", wt.UINT),
                ("lbColor", wt.COLORREF),
                ("lbHatch", ctypes.c_void_p)]


def _ring_pen(rgb, width: int) -> int:
    """平头几何画笔：圆弧端点不会额外探出，50% 就是正好 180°。"""
    lb = _LogBrush()
    lb.lbStyle = 0  # BS_SOLID
    lb.lbColor = COLORREF(*rgb)
    lb.lbHatch = 0
    gdi32.ExtCreatePen.argtypes = [wt.DWORD, ctypes.c_int,
                                   ctypes.POINTER(_LogBrush),
                                   wt.DWORD, ctypes.c_void_p]
    gdi32.ExtCreatePen.restype = wt.HPEN
    return gdi32.ExtCreatePen(0x00010000 | 0x00000200, width,
                              ctypes.byref(lb), 0, None)


def make_battery_icon(level: Optional[int], light: bool,
                      charging: bool = False, size: int = 32) -> int:
    """size×size 的电量圆环图标，4 倍超采样后缩小，边缘更圆。"""
    SS = 4
    big = size * SS
    hdc_screen = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc_screen)
    hbm_small = 0

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = big
    bmi.bmiHeader.biHeight = -big
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbm_big = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(memdc, hbm_big)
    try:
        key = COLORREF(255, 0, 255)
        bg = gdi32.CreateSolidBrush(key)
        rect = wt.RECT(0, 0, big, big)
        user32.FillRect(memdc, ctypes.byref(rect), bg)
        gdi32.DeleteObject(bg)

        # 电量：暗色轨道圆 + 顺时针进度弧
        s = big / 32.0
        cx = cy = big / 2.0
        radius = 12.0 * s
        thick = max(2, int(round(4 * s)))
        track = (95, 98, 105) if not light else (175, 180, 188)
        pen_track = _ring_pen(track, thick)
        gdi32.SelectObject(memdc, pen_track)
        null_brush = gdi32.GetStockObject(5)  # NULL_BRUSH
        gdi32.SelectObject(memdc, null_brush)
        gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                      int(cx + radius), int(cy + radius))
        if level is not None:
            if charging:
                r, g, b = 34, 197, 94          # 充电：弧线绿色
            else:
                r, g, b = _level_color(level)
            pen_arc = _ring_pen((r, g, b), thick)
            gdi32.SelectObject(memdc, pen_arc)
            if level >= 100:
                gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                              int(cx + radius), int(cy + radius))
            else:
                theta = 2.0 * math.pi * max(2, min(100, level)) / 100.0
                ex = cx + radius * math.cos(-math.pi / 2 + theta)
                ey = cy + radius * math.sin(-math.pi / 2 + theta)
                # Arc 逆时针画：从终点绕回顶点，等价于从顶点顺时针的进度弧
                gdi32.Arc(memdc, int(cx - radius), int(cy - radius),
                          int(cx + radius), int(cy + radius),
                          int(round(ex)), int(round(ey)), int(cx), int(cy - radius))
            gdi32.DeleteObject(pen_arc)

        gdi32.SelectObject(memdc, old)
        gdi32.GdiFlush()
        raw = ctypes.string_at(bits, big * big * 4)

        # 4×4 覆盖率降采样：每个目标像素统计非洋红比例作为 alpha
        small = bytearray(size * size * 4)
        for y in range(size):
            for x in range(size):
                cnt = 0
                rs = gs = bs = 0
                for yy in range(y * SS, (y + 1) * SS):
                    base = yy * big * 4 + x * SS * 4
                    for xx in range(SS):
                        i = base + xx * 4
                        b_, g_, r_ = raw[i], raw[i + 1], raw[i + 2]
                        if (r_, g_, b_) != (255, 0, 255):
                            cnt += 1
                            rs += r_
                            gs += g_
                            bs += b_
                o = (y * size + x) * 4
                if cnt:
                    small[o] = bs // cnt
                    small[o + 1] = gs // cnt
                    small[o + 2] = rs // cnt
                    small[o + 3] = cnt * 255 // (SS * SS)

        bmi2 = BITMAPINFO()
        bmi2.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi2.bmiHeader.biWidth = size
        bmi2.bmiHeader.biHeight = -size
        bmi2.bmiHeader.biPlanes = 1
        bmi2.bmiHeader.biBitCount = 32
        bmi2.bmiHeader.biCompression = 0
        bits2 = ctypes.c_void_p()
        hbm_small = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi2), 0,
                                           ctypes.byref(bits2), None, 0)
        gdi32.SelectObject(memdc, hbm_small)
        ctypes.memmove(bits2, bytes(small), len(small))

        mask = gdi32.CreateBitmap(size, size, 1, 1, None)
        ii = ICONINFO()
        ii.fIcon = 1
        ii.hbmMask = mask
        ii.hbmColor = hbm_small
        icon = user32.CreateIconIndirect(ctypes.byref(ii))
        gdi32.DeleteObject(mask)
        return icon
    finally:
        if hbm_small:
            gdi32.DeleteObject(hbm_small)
        gdi32.DeleteObject(hbm_big)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc_screen)

# ===========================================================================
# 开机自启 / 桌面链接 / 快捷方式（纯 ctypes COM）
# ===========================================================================
def _shell_folder(csidl: int) -> str:
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


def desktop_dir() -> str:
    return _shell_folder(0x0010)  # CSIDL_DESKTOPDIRECTORY


def startup_dir() -> str:
    return _shell_folder(0x0007)  # CSIDL_STARTUP


def startup_lnk() -> str:
    return os.path.join(startup_dir(), "2.4G.lnk")


def legacy_startup_lnk() -> str:
    return os.path.join(startup_dir(), "电量托盘.lnk")


def startup_enabled() -> bool:
    return (os.path.isfile(startup_lnk())
            or os.path.isfile(legacy_startup_lnk()))


def project_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def script_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def pythonw_path() -> str:
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(cand):
            return cand
    if exe.lower().endswith("pythonw.exe"):
        return exe
    return exe


def _com_method(obj, index, restype, *argtypes):
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vt[index])


def create_shortcut(lnk_path: str, target: str, args: str = "",
                    workdir: str = "", desc: str = "",
                    icon: str = "") -> None:
    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    CLSID_ShellLink = GUID("{00021401-0000-0000-C000-000000000046}")
    IID_IShellLinkW = GUID("{000214F9-0000-0000-C000-000000000046}")
    IID_IPersistFile = GUID("{0000010B-0000-0000-C000-000000000046}")

    psl = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1,
                                ctypes.byref(IID_IShellLinkW), ctypes.byref(psl))
    if hr != 0 or not psl:
        raise RuntimeError(f"CoCreateInstance(CLSID_ShellLink) 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    # IShellLinkW: SetPath=20, SetArguments=11, SetWorkingDirectory=9,
    #              SetDescription=7, SetIconLocation=17
    _com_method(psl, 20, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(target))
    if args:
        _com_method(psl, 11, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(args))
    if workdir:
        _com_method(psl, 9, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(workdir))
    if desc:
        _com_method(psl, 7, ctypes.c_long, ctypes.c_wchar_p)(psl, ctypes.c_wchar_p(desc))
    if icon:
        _com_method(psl, 17, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
            psl, ctypes.c_wchar_p(icon), 0)

    ppf = ctypes.c_void_p()
    _com_method(psl, 0, ctypes.c_long, ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p))(
        psl, ctypes.byref(IID_IPersistFile), ctypes.byref(ppf))
    if not ppf:
        _com_method(psl, 2, ctypes.c_ulong)(psl)
        raise RuntimeError("QueryInterface(IPersistFile) 失败")
    # IPersistFile: Save=6, SaveCompleted=7
    _com_method(ppf, 6, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
        ppf, ctypes.c_wchar_p(lnk_path), 1)
    _com_method(ppf, 2, ctypes.c_ulong)(ppf)
    _com_method(psl, 2, ctypes.c_ulong)(psl)
    ole32.CoUninitialize()


def install_startup() -> bool:
    path = startup_lnk()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if getattr(sys, "frozen", False):
        create_shortcut(path, script_path(), "", project_dir(),
                        "2.4G", script_path())
    else:
        create_shortcut(path, pythonw_path(), f'"{script_path()}"',
                        project_dir(), "2.4G", pythonw_path())
    if os.path.isfile(path):
        try:
            os.remove(legacy_startup_lnk())
        except FileNotFoundError:
            pass
    return os.path.isfile(path)


def remove_startup() -> None:
    for p in (startup_lnk(), legacy_startup_lnk()):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def migrate_startup_link() -> None:
    """把旧的“电量托盘.lnk”迁移成“2.4G.lnk”，指向当前程序。"""
    if os.path.isfile(legacy_startup_lnk()):
        try:
            install_startup()
        except Exception:
            pass


kernel32.CreateSymbolicLinkW.restype = wt.BOOL
kernel32.CreateSymbolicLinkW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]


def _create_dir_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x1 | 0x2):
            if os.path.isdir(link):
                return True
    except Exception:
        pass
    # 降级：junction
    try:
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, shell=False)
        return os.path.isdir(link)
    except Exception:
        return False


def _create_file_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x2):
            if os.path.isfile(link):
                return True
    except Exception:
        pass
    try:
        os.link(target, link)
        return os.path.isfile(link)
    except Exception:
        pass
    # 跨卷 / 无号链接权限：后化成 .lnk（仍昌向脚朚链接，不昉朼
    try:
        create_shortcut(link + ".lnk", target, "", project_dir(),
                        "外设电量启动脚本", pythonw_path())
        return os.path.isfile(link + ".lnk")
    except Exception:
        return False


def install_desktop_links() -> list[str]:
    created = []
    desk = desktop_dir()
    if getattr(sys, "frozen", False):
        lnk = os.path.join(desk, "外设电量.lnk")
        if not os.path.exists(lnk):
            try:
                create_shortcut(lnk, script_path(), "", project_dir(),
                                "外设电量", script_path())
                created.append(lnk)
            except Exception:
                pass
        return created
    dlink = os.path.join(desk, "外设电量")
    flink = os.path.join(desk, "my_devices.py")
    if not os.path.exists(dlink):
        if _create_dir_link(dlink, project_dir()):
            created.append(dlink)
    if not os.path.exists(flink):
        if _create_file_link(flink, script_path()):
            created.append(flink if os.path.exists(flink) else flink + ".lnk")
    return created


# ===========================================================================
# 配置（设备、协议、上次电量、刷新间隔；不是日志）
# ===========================================================================
def config_dir() -> str:
    base = os.environ.get("APPDATA") or project_dir()
    return os.path.join(base, "BatteryTray")


def config_file() -> str:
    return os.path.join(config_dir(), "config.json")


def clamp_interval(sec) -> int:
    try:
        sec = int(sec)
    except Exception:
        return 60
    return max(30, min(1800, sec))


def load_config() -> dict:
    try:
        with open(config_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(config_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def custom_names() -> dict:
    names = load_config().get("names")
    return names if isinstance(names, dict) else {}


def usbpcap_path() -> str:
    p = load_config().get("usbpcap")
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return ""


def set_usbpcap_path(path: str) -> None:
    cfg = load_config()
    cfg["usbpcap"] = path
    save_config(cfg)


def user_protocols() -> dict:
    v = load_config().get("protocols")
    return v if isinstance(v, dict) else {}


PROTO_KEYS = ("invert", "requests", "offset", "v", "req_kinds", "out_len",
              "reply", "reply_id", "in_len", "match")


def save_user_protocol(vid: int, pid: int, proto: dict) -> None:
    cfg = load_config()
    protos = cfg.get("protocols")
    if not isinstance(protos, dict):
        protos = {}
    saved = {"kind": "captured"}
    for k in PROTO_KEYS:
        if k in proto:
            saved[k] = proto[k]
    saved["invert"] = bool(proto.get("invert", False))
    saved["requests"] = list(proto.get("requests", []))
    saved["offset"] = int(proto.get("offset", 2))
    protos[f"{vid:04X}:{pid:04X}"] = saved
    cfg["protocols"] = protos
    save_config(cfg)


_DESCRIPTOR_PREFIXES = (
    b"\x12\x01",  # 设备描述符
    b"\x09\x02",  # 配置描述符
    b"\x09\x04",  # 接口描述符
    b"\x07\x05",  # 端点描述符
    b"\x09\x21",  # HID 描述符
    b"\x05\x01",  # 报告描述符：通用桌面
    b"\x05\x0c",  # 报告描述符：消费控制
    b"\x06\x00",  # 报告描述符：厂商页
    b"\x0a\x06",  # 设备限定符
    b"\x07\x07",  # Other Speed 配置
    b"\x0f\x05",  # BOS 描述符
)


def _looks_descriptor(d: bytes) -> bool:
    return len(d) >= 2 and d[:2] in _DESCRIPTOR_PREFIXES


def _looks_control_setup(d: bytes, ep: int) -> bool:
    """EP0 上不承载电量的标准控制请求。"""
    if ep not in (0x00, 0x80) or len(d) != 8:
        return False
    # GET_DESCRIPTOR / SET_CONFIGURATION / SET_INTERFACE / CLEAR_FEATURE
    return ((d[0] == 0x80 and d[1] == 0x06)
            or (d[0] == 0x00 and d[1] == 0x09)
            or (d[0] == 0x01 and d[1] == 0x0B)
            or (d[0] == 0x02 and d[1] == 0x01))


def _looks_string_descriptor(d: bytes, ep: int) -> bool:
    """EP0 上的字符串描述符帧：设备身份文本（厂商/产品/序列号），
    只是枚举期从设备读回来的 UTF-16 文本，绝不承载电量。
    不过滤会拿文本里的字母撞值，例如 'd' = 0x64 = 100%。"""
    if ep != 0x80:
        return False
    # 字符串描述符本体：bLength 必须等于实际长度，bDescriptorType = 0x03
    if len(d) >= 2 and d[1] == 0x03 and d[0] == len(d):
        return True
    # GET_DESCRIPTOR 的 setup 阶段（80 06 ...，固定 8 字节）：只是请求包，不是数据
    return len(d) == 8 and d[0] == 0x80 and d[1] == 0x06


def _split_frames(frames: list):
    ins = []
    outs = []
    for idx, (ep, _tr, d) in enumerate(frames):
        if not d:
            continue
        if ep & 0x80:
            if len(d) < 4:
                continue
            if ep == 0x80 and (_looks_descriptor(d)
                               or _looks_control_setup(d, ep)
                               or _looks_string_descriptor(d, ep)):
                continue
            if not any(d):
                continue
            ins.append((idx, d))
        elif ep != 0 and len(d) >= 3:
            outs.append((idx, d))
    return ins, outs


def _match_offsets(ins: list, level: int) -> set:
    out = set()
    for _idx, d in ins:
        for off in range(1, min(len(d), 64)):
            if d[off] == level:
                out.add(off)
    return out


# 抓包里合成的控制传输帧：transfer 字段标明来源，数据统一成 Windows 缓冲区布局
# （首字节是报告 ID，设备不用 ID 时补 0），与 HidD_* 读写的缓冲区一一对应
XFER_SET_OUTPUT = 0x12   # SET_REPORT(Output) 的报告本体
XFER_SET_FEATURE = 0x13  # SET_REPORT(Feature) 的报告本体
XFER_GET_INPUT = 0x21    # GET_REPORT(Input) 的回复
XFER_GET_FEATURE = 0x23  # GET_REPORT(Feature) 的回复
EP_CTRL_OUT = 0x0F       # 合成帧用的伪端点：非 0，不会被当成描述符过滤
EP_CTRL_IN = 0x8F
REQ_KIND = {XFER_SET_OUTPUT: "set_out", XFER_SET_FEATURE: "set_feat"}
REPLY_KIND = {XFER_GET_INPUT: "get_input", XFER_GET_FEATURE: "feature"}


def _requests_at(frames: list, ins: list, outs: list,
                 off: int, level: int) -> list:
    """找出电量回复之前发出的请求帧，返回 [(字节, 类型)]，最多 6 条。"""
    reqs = []

    def add(idx, d):
        item = (d, REQ_KIND.get(frames[idx][1], "out"))
        if item not in reqs:
            reqs.append(item)
    oi = 0
    last_idx = -1
    for idx, d in ins:
        while oi < len(outs) and outs[oi][0] < idx:
            last_idx = outs[oi][0]
            oi += 1
        if len(d) <= off or d[off] != level:
            continue
        if last_idx >= 0 and idx - last_idx <= 80:
            add(last_idx, frames[last_idx][2])
    if not reqs:
        prefixes = {d[:2] for _i, d in ins
                    if len(d) > off and d[off] == level}
        for i, d in outs:
            if d[:2] in prefixes:
                add(i, d)
    if not reqs:
        for i, d in outs:
            add(i, d)
    return reqs[:6]


def _reply_info(frames: list, ins: list, off: int, level: int) -> dict:
    """电量回复的形态：来源（输入报告 / GET_REPORT）、报告 ID、
    线上长度，以及电量字节之前恒定不变的前两个字节（用于过滤别的报告）。"""
    hits = [(i, d) for i, d in ins if len(d) > off and d[off] == level]
    if not hits:
        return {}
    i0, d0 = hits[0]
    kind = REPLY_KIND.get(frames[i0][1], "input")
    info = {"reply": kind}
    if kind == "input":
        info["in_len"] = len(d0)
    else:
        info["reply_id"] = d0[0]
    same = [d for i, d in hits
            if REPLY_KIND.get(frames[i][1], "input") == kind]
    info["match"] = [[p, d0[p]] for p in range(min(off, 2))
                     if all(len(d) > p and d[p] == d0[p] for d in same)]
    return info


def _proto_at(reqs: list, off: int, reply: dict) -> dict:
    proto = {
        "v": 2,
        "requests": [" ".join(f"{b:02X}" for b in d[:64]) for d, _k in reqs],
        "req_kinds": [k for _d, k in reqs],
        "out_len": max((len(d) for d, k in reqs if k == "out"), default=0),
        "offset": off,
    }
    proto.update(reply)
    return proto


def infer_single_ex(level: int, frames: list):
    """单次抓取：唯一字节位置直接采用，多个位置返回 ambiguous。"""
    level = int(level)
    if level <= 0:
        return None, "none"
    ins, outs = _split_frames(frames)
    if not ins or not outs:
        return None, "none"
    offs = _match_offsets(ins, level)
    if not offs:
        return None, "none"
    if len(offs) > 1:
        return None, "ambiguous"
    off = next(iter(offs))
    reqs = _requests_at(frames, ins, outs, off, level)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off, _reply_info(frames, ins, off, level)), "ok"


def infer_two(level_a: int, frames_a: list,
              level_b: int, frames_b: list):
    """两次不同电量：某字节的差值等于两次电量差值，唯一则采用。"""
    level_a = int(level_a)
    level_b = int(level_b)
    if level_a <= 0 or level_b <= 0 or level_a == level_b:
        return None, "none"
    ins_a, outs_a = _split_frames(frames_a)
    ins_b, outs_b = _split_frames(frames_b)
    if not ins_a or not ins_b:
        return None, "none"
    delta = level_b - level_a
    candidates = []
    for off in range(1, 64):
        va = {d[off] for _i, d in ins_a if len(d) > off}
        vb = {d[off] for _i, d in ins_b if len(d) > off}
        if any((y - x) == delta for x in va for y in vb):
            candidates.append(off)
    if not candidates:
        return None, "none"
    if len(candidates) > 1:
        return None, "ambiguous"
    off = candidates[0]
    frames, ins, level = frames_a, ins_a, level_a
    reqs = _requests_at(frames_a, ins_a, outs_a, off, level_a)
    if not reqs:
        frames, ins, level = frames_b, ins_b, level_b
        reqs = _requests_at(frames_b, ins_b, outs_b, off, level_b)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off, _reply_info(frames, ins, off, level)), "ok"


def _parse_requests(proto: dict) -> list:
    """[(字节, 类型)]；旧协议没有类型，一律当中断 OUT。"""
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    kinds = proto.get("req_kinds")
    if not isinstance(kinds, list):
        kinds = []
    out = []
    for i, s in enumerate(raw):
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if len(b) >= 2:
            out.append((b, kinds[i] if i < len(kinds) else "out"))
    return out


def _reply_value(rep: Optional[bytes], off: int, shift: int, match: list):
    """回复里取电量：返回 (电量, 严格匹配?)；不符合就返回 None。"""
    if not rep or len(rep) <= off + shift:
        return None
    for n, (pos, val) in enumerate(match):
        if len(rep) <= pos + shift or rep[pos + shift] != val:
            if n == 0:
                return None
            v = rep[off + shift]
            return (v, False) if 0 <= v <= 100 else None
    v = rep[off + shift]
    return (v, True) if 0 <= v <= 100 else None


def _proto_reading(proto: dict, val: int, rep: bytes, how: str) -> Reading:
    if proto.get("invert"):
        val = 100 - val
    return Reading(ok=True, level=val, source="抓包协议",
                   detail=f"{how} {rep[:8].hex(' ')}", raw=rep)


def _read_user_protocol(ifaces: list, proto: dict):
    """用抓包得到的协议读一次电量。v2 协议按记录的请求/回复形态回放，
    旧协议走原来的“写 Output、读 Input”老路。"""
    if proto.get("v", 1) >= 2:
        return _read_v2_protocol(ifaces, proto)
    return _read_legacy_protocol(ifaces, proto)


def _read_v2_protocol(ifaces: list, proto: dict):
    off = int(proto.get("offset", 2))
    reqs = _parse_requests(proto)
    if off < 1 or not reqs:
        return None
    reply = proto.get("reply", "input")
    match = [tuple(m) for m in proto.get("match", []) if len(m) == 2]
    in_len = int(proto.get("in_len", 0) or 0)
    out_len = int(proto.get("out_len", 0) or 0)
    rid = int(proto.get("reply_id", 0) or 0)
    for req, kind in reqs:
        for d in ifaces:
            if kind == "set_feat":
                if d.feature_len < len(req):
                    continue
                buf = req + bytes(d.feature_len - len(req))
            elif kind == "set_out":
                if d.output_len < len(req):
                    continue
                buf = req + bytes(d.output_len - len(req))
            else:
                # 线上帧比 Windows 输出缓冲区少 1 字节：设备不用报告 ID，补 0
                full = out_len or len(req)
                lead = b"\0" if full == d.output_len - 1 else b""
                buf = lead + req
                if d.output_len < len(buf):
                    continue
                buf += bytes(d.output_len - len(buf))
            if reply == "input":
                if d.input_len <= off:
                    continue
                shift = 1 if in_len and in_len == d.input_len - 1 else 0
            elif reply == "feature":
                if d.feature_len <= off:
                    continue
                shift = 0
            else:
                if d.input_len <= off:
                    continue
                shift = 0
            dev = None
            try:
                dev = HidDevice(d.path, write=True)
                if reply == "input":
                    dev.drain()
                if kind == "set_feat":
                    if not dev.set_feature(buf):
                        continue
                elif kind == "set_out":
                    if not dev.set_output_report(buf):
                        continue
                else:
                    dev.write_output(buf)
                loose = None
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    if reply == "input":
                        rep = dev.read_input(max(64, d.input_len), 250)
                    else:
                        time.sleep(0.05)
                        rep = (dev.get_feature_report(rid, d.feature_len)
                               if reply == "feature" else
                               dev.get_input_report(rid, d.input_len))
                    got = _reply_value(rep, off, shift, match)
                    if got is None:
                        continue
                    if got[1]:
                        return _proto_reading(proto, got[0], rep, "回复")
                    if loose is None:
                        loose = (got[0], rep)
                if loose is not None:
                    return _proto_reading(proto, loose[0], loose[1], "回复")
            except Exception:
                pass
            finally:
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass
    return None


def _read_legacy_protocol(ifaces: list, proto: dict):
    """旧版抓包协议（没有 v 字段）：写 Output 报告、读 Input 报告。"""
    off = int(proto.get("offset", 2))
    if off < 1:
        return None
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    reqs = []
    for s in raw:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if len(b) >= 2:
            reqs.append(b)
    if not reqs:
        return None
    for req in reqs:
        for d in ifaces:
            if d.output_len < len(req) or d.input_len <= off:
                continue
            dev = None
            try:
                dev = HidDevice(d.path, write=True)
                buf = bytes(req) + bytes(max(0, d.output_len - len(req)))
                dev.drain()
                dev.write_output(buf)
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    rep = dev.read_input(max(64, d.input_len), 250)
                    if not rep or len(rep) <= off:
                        continue
                    if 0 <= rep[off] <= 100:
                        val = rep[off]
                        if proto.get("invert"):
                            val = 100 - val
                        return Reading(ok=True, level=val,
                                       source="抓包协议",
                                       detail=f"回复 {rep[:8].hex(' ')}",
                                       raw=rep)
            except Exception:
                pass
            finally:
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass
    return None


def _listen_user_protocol(ifaces: list, proto: dict,
                          seconds: float = 1.5):
    """被动监听输入报告：不发请求，只看设备自己上报的字节。"""
    if proto.get("v", 1) < 2:
        return _listen_legacy_protocol(ifaces, proto, seconds)
    if proto.get("reply", "input") != "input":
        return None
    off = int(proto.get("offset", 2))
    match = [tuple(m) for m in proto.get("match", []) if len(m) == 2]
    if off < 1 or not match:
        return None  # 没有报告特征就不靠猜，宁可读不到也不读错
    in_len = int(proto.get("in_len", 0) or 0)
    for d in ifaces:
        if d.input_len <= off:
            continue
        shift = 1 if in_len and in_len == d.input_len - 1 else 0
        dev = None
        try:
            dev = HidDevice(d.path, write=False)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                rep = dev.read_input(max(64, d.input_len), 250)
                got = _reply_value(rep, off, shift, match)
                if got is not None and got[1]:
                    r = _proto_reading(proto, got[0], rep, "监听到报告")
                    r.source = "被动监听输入报告"
                    return r
        except Exception:
            pass
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
    return None


def _listen_legacy_protocol(ifaces: list, proto: dict,
                          seconds: float = 1.5):
    """被动监听输入报告：不发请求，只看设备自己上报的字节。"""
    off = int(proto.get("offset", 2))
    if off < 1:
        return None
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    reqs = []
    for s in raw:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if b:
            reqs.append(b)
    req_ids = {b[0] for b in reqs}
    for d in ifaces:
        if d.input_len <= off:
            continue
        dev = None
        try:
            dev = HidDevice(d.path, write=False)
        except Exception:
            continue
        try:
            deadline = time.monotonic() + seconds
            seen = {}
            first = None
            while time.monotonic() < deadline:
                rep = dev.read_input(max(64, d.input_len), 250)
                if not rep or len(rep) <= off:
                    continue
                val = rep[off]
                if not (0 <= val <= 100):
                    continue
                if first is None:
                    first = (val, rep)
                if req_ids and rep[0] in req_ids:
                    return Reading(ok=True, level=val,
                                   source="被动监听输入报告",
                                   detail=f"报告 {rep[:8].hex(' ')}",
                                   raw=rep)
                seen[val] = seen.get(val, 0) + 1
                if seen[val] >= 2:
                    return Reading(ok=True, level=val,
                                   source="被动监听输入报告",
                                   detail=f"连续报告 {rep[:8].hex(' ')}",
                                   raw=rep)
            if first is not None:
                return Reading(ok=True, level=first[0],
                               source="被动监听输入报告",
                               detail=f"单次报告 {first[1][:8].hex(' ')}",
                               raw=first[1])
        except Exception:
            pass
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
    return None


def _validate_capture_protocol(proto: dict, vid: int, pid: int,
                              level: int):
    """返回 (模式, 回读结果)：direct / invert / None。"""
    try:
        ifaces = [d for d in enumerate_hid()
                  if d.vid == vid and d.pid == pid]
    except Exception:
        ifaces = []
    if not ifaces:
        return None, None
    r = _read_user_protocol(ifaces, proto)
    if not r or not r.ok:
        r = _listen_user_protocol(ifaces, proto)
    if not r or not r.ok or r.level is None:
        return None, r
    raw = int(r.level)
    if abs(raw - int(level)) <= 2:
        return "direct", r
    if abs((100 - raw) - int(level)) <= 2:
        return "invert", r
    return None, r


def _iter_pcap_ex(path: str):
    """流式读取 USBPcap pcap，逐包产出
    (bus, dev, ep, transfer, payload, info, stage)；stage 只有控制传输才有，否则 -1。"""
    try:
        f = open(path, "rb")
    except Exception:
        return
    with f:
        head = f.read(24)
        if len(head) < 24:
            return
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                return
            try:
                _ts, _tu, incl, _orig = struct.unpack("<IIII", ph)
            except Exception:
                return
            if incl <= 0 or incl > 64 * 1024 * 1024:
                return
            pkt = f.read(incl)
            if len(pkt) < incl:
                return
            if len(pkt) < 27:
                continue
            try:
                hlen = struct.unpack_from("<H", pkt, 0)[0]
            except Exception:
                continue
            if not (27 <= hlen <= 600) or len(pkt) < hlen:
                continue
            dlen = struct.unpack_from("<I", pkt, 23)[0]
            payload = pkt[hlen:hlen + dlen]
            bus = struct.unpack_from("<H", pkt, 17)[0]
            dev = struct.unpack_from("<H", pkt, 19)[0]
            stage = pkt[27] if hlen >= 28 and pkt[22] == 2 else -1
            yield bus, dev, pkt[21], pkt[22], payload, pkt[16], stage


def _iter_pcap_packets(path: str):
    """逐包产出 (bus, dev, ep, transfer, payload)。"""
    for bus, dev, ep, tr, payload, _info, _stage in _iter_pcap_ex(path):
        yield bus, dev, ep, tr, payload


def scan_pcap_map(path: str) -> dict:
    """返回 {(bus, device): (vid, pid)}，来自 pcap 里的设备描述符。"""
    out = {}
    for bus, dev, _ep, _tr, payload in _iter_pcap_packets(path):
        for i in range(0, max(0, len(payload) - 11)):
            if payload[i] == 18 and payload[i + 1] == 1:
                vid = payload[i + 8] | (payload[i + 9] << 8)
                pid = payload[i + 10] | (payload[i + 11] << 8)
                if vid and pid:
                    out[(bus, dev)] = (vid, pid)
                break
    return out


def scan_pcap_devices(path: str) -> list:
    """返回 pcap 里出现过的 [(vid, pid)]。"""
    seen = []
    for v in scan_pcap_map(path).values():
        if v not in seen:
            seen.append(v)
    return seen


def _win_layout(report_id: int, body: bytes) -> bytes:
    """控制传输的报告本体 → Windows HidD_* 缓冲区（首字节为报告 ID）。"""
    if report_id and body[:1] == bytes((report_id,)):
        return bytes(body)
    return bytes((report_id,)) + bytes(body)


def extract_pcap_reports(path: str, target: set = None,
                         limit: int = None) -> list:
    """流式提取 HID 帧 [(ep, transfer, 数据)]；target 为 {(bus, device)} 时只收目标设备。
    HID 类控制请求（SET_REPORT / GET_REPORT）合成为带 XFER_* 标记的帧，
    兼容 setup 与数据同包、分包两种记录方式。"""
    out = []
    pending = {}

    def add(frame):
        if not out or out[-1][1:] != frame[1:]:
            out.append(frame)
        return bool(limit and len(out) >= limit)

    for bus, dev, ep, transfer, payload, _info, stage in _iter_pcap_ex(path):
        if target is not None and (bus, dev) not in target:
            continue
        key = (bus, dev)
        if ep & 0x7F == 0:
            if (stage in (0, -1) and len(payload) >= 8
                    and payload[0] in (0x21, 0xA1)
                    and payload[1] in (0x09, 0x01)):
                setup = bytes(payload[:8])
                rid, rtype = payload[2], payload[3]
                wlen = payload[6] | (payload[7] << 8)
                body = payload[8:8 + wlen]
                if payload[1] == 0x09 and body:
                    if rtype in (2, 3) and add((
                            EP_CTRL_OUT, XFER_SET_OUTPUT if rtype == 2
                            else XFER_SET_FEATURE, _win_layout(rid, body))):
                        break
                else:
                    pending[key] = (payload[1], rid, rtype, wlen, setup)
                continue
            p = pending.get(key)
            if p and payload:
                req, rid, rtype, wlen, setup = p
                body = payload[8:] if payload[:8] == setup else payload
                body = body[:wlen]
                if req == 0x09 and not ep & 0x80 and rtype in (2, 3):
                    pending.pop(key, None)
                    if body and add((EP_CTRL_OUT, XFER_SET_OUTPUT if rtype == 2
                                     else XFER_SET_FEATURE,
                                     _win_layout(rid, body))):
                        break
                    continue
                if req == 0x01 and ep & 0x80 and rtype in (1, 3):
                    pending.pop(key, None)
                    if body and add((EP_CTRL_IN, XFER_GET_INPUT if rtype == 1
                                     else XFER_GET_FEATURE,
                                     _win_layout(rid, body))):
                        break
                    continue
        if len(payload) < 4:
            continue
        if add((ep, transfer, bytes(payload))):
            break
    return out


def extract_pcap_for(path: str, vid: int, pid: int):
    """只提取目标 VID:PID 的帧；找不到映射时退回全部帧。"""
    mapping = scan_pcap_map(path)
    targets = {k for k, v in mapping.items() if v == (vid, pid)} if vid else set()
    frames = extract_pcap_reports(path, targets or None)
    return frames, mapping


def format_reports(reports: list) -> str:
    lines = ["# endpoint dir transfer length data"]
    for ep, transfer, data in reports:
        direction = "IN" if ep & 0x80 else "OUT"
        hexs = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{ep:04X} {direction} t={transfer} "
                     f"len={len(data)} {hexs}")
    return "\n".join(lines)


def set_custom_name(key: str, name: str) -> None:
    cfg = load_config()
    names = cfg.get("names")
    if not isinstance(names, dict):
        names = {}
    name = (name or "").strip()
    if name:
        names[key] = name
    else:
        names.pop(key, None)
    cfg["names"] = names
    save_config(cfg)


# ===========================================================================
# CLI
# ===========================================================================
def read_saved_all(devices: Optional[list] = None) -> dict[str, Reading]:
    """读一遍已添加的设备（托盘刷新与 --once 共用），没读到的沿用上次电量。"""
    devices = load_saved_devices() if devices is None else devices
    ifaces = enumerate_hid()
    res = {device_key(d["vid"], d["pid"]): read_saved_device(d, ifaces)
           for d in devices}
    return apply_last_known(res)


def print_once():
    res = read_saved_all()
    if not res:
        print("还没有添加设备：先运行托盘程序，插入接收器后在弹窗里点「添加」。")
        return
    for r in res.values():
        if r.ok:
            suffix = f"；{r.detail}" if r.stale else f"（{r.source}）"
            print(f"{r.name}：{r.level}%{suffix}")
        else:
            print(f"{r.name}：{r.text}（{r.detail}）")


def list_devices():
    for d in enumerate_hid():
        print(d.label, "|", d.manufacturer, "|", d.product, "|", d.instance)


def install_all():
    ok = install_startup()
    links = install_desktop_links()
    print("开机自启", startup_lnk(), "（已创建）" if ok else "（失败）")
    for l in links:
        print("桌面链接", l)
    if not links:
        print("桌面链接：已存在，未重创建")


CREATE_NO_WINDOW = 0x08000000

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = w.HANDLE
k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                            w.DWORD, w.DWORD, w.HANDLE]


def _helper_scan_devices(path):
    out = []
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return out
    if len(data) < 24:
        return out
    off = 24
    n = len(data)
    while off + 16 <= n:
        try:
            _ts, _tu, incl, _orig = struct.unpack_from("<IIII", data, off)
        except Exception:
            break
        off += 16
        if incl <= 0 or off + incl > n:
            break
        pkt = data[off:off + incl]
        off += incl
        if len(pkt) < 31:
            continue
        hlen = struct.unpack_from("<H", pkt, 0)[0]
        if not (27 <= hlen <= 600) or len(pkt) < hlen:
            continue
        dlen = struct.unpack_from("<I", pkt, 23)[0]
        payload = pkt[hlen:hlen + dlen]
        for i in range(0, len(payload) - 17):
            if payload[i] == 18 and payload[i + 1] == 1:
                vid = payload[i + 8] | (payload[i + 9] << 8)
                pid = payload[i + 10] | (payload[i + 11] << 8)
                if vid and pid and (vid, pid) not in out:
                    out.append((vid, pid))
    return out


def _helper_hub_exists(n):
    h = k32.CreateFileW("\\\\.\\USBPcap%d" % n, 0, 3, None, 3, 0, None)
    err = ctypes.get_last_error()
    if h and h != w.HANDLE(-1).value:
        k32.CloseHandle(h)
        return True
    return err == 5


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation",
                 _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_JOB = None


def _setup_job():
    """Job：helper 退出时连带收掉 USBPcapCMD。"""
    global _JOB
    try:
        k32.CreateJobObjectW.restype = w.HANDLE
        k32.GetCurrentProcess.restype = w.HANDLE
        k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                    ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
        _JOB = job
    except Exception:
        _JOB = None


def _helper_main():
    _setup_job()
    (cp, vid_s, pid_s, outdir, stop_flag, status_file) = sys.argv[1:7]
    vid = int(vid_s, 16)
    pid = int(pid_s, 16)

    def status(state, hub=0, file=""):
        tmp = status_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"state": state, "hub": hub, "file": file}, f)
            os.replace(tmp, status_file)
        except Exception:
            pass

    hubs = [n for n in range(1, 7) if _helper_hub_exists(n)]
    status("probing")
    any_bytes = False
    found = 0
    if vid:
        for hub in hubs:
            probe = os.path.join(outdir, "probe%d.pcap" % hub)
            try:
                os.remove(probe)
            except Exception:
                pass
            try:
                p = subprocess.Popen(
                    [cp, "-d", "\\\\.\\USBPcap%d" % hub, "-A",
                     "--inject-descriptors", "-o", probe],
                    creationflags=CREATE_NO_WINDOW,
                    stderr=subprocess.DEVNULL)
            except Exception:
                continue
            time.sleep(4.0)
            try:
                p.terminate()
                p.wait(3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                if os.path.getsize(probe) > 0:
                    any_bytes = True
            except Exception:
                pass
            devs = _helper_scan_devices(probe)
            if (vid, pid) in devs:
                found = hub
                break
        if not found:
            status("notraffic" if any_bytes else "busy")
            return
    else:
        found = hubs[0] if hubs else 1

    status("capturing", found)
    final = os.path.join(outdir, "capture.pcap")
    try:
        p = subprocess.Popen(
            [cp, "-d", "\\\\.\\USBPcap%d" % found, "-A",
             "--inject-descriptors", "-o", final],
            creationflags=CREATE_NO_WINDOW,
            stderr=subprocess.DEVNULL)
    except Exception:
        status("error")
        return
    started = time.time()
    while not os.path.exists(stop_flag):
        if p.poll() is not None:
            break
        if time.time() - started > 180:
            break
        try:
            if os.path.getsize(final) > 256 * 1024 * 1024:
                break
        except Exception:
            pass
        time.sleep(0.2)
    try:
        p.terminate()
        p.wait(3)
    except Exception:
        try:
            p.kill()
            p.wait(2)
        except Exception:
            pass
    status("done", found, final)


def _helper_run(args: list):
    old = sys.argv
    sys.argv = ["capture_helper.py"] + list(args)
    try:
        _helper_main()
    finally:
        sys.argv = old


def main(argv):
    if argv and argv[0] == "--capture-helper":
        _helper_run(argv[1:])
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--once" in argv:
        print_once()
        return 0
    if "--list" in argv:
        list_devices()
        return 0
    if "--install" in argv:
        install_all()
        return 0
    return run_gui(panel_only="--panel" in argv)


# ===========================================================================
# Fluent 界面：配色、字体与抗锯齿绘制
# ===========================================================================
import base64
import zlib
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import filedialog, messagebox, simpledialog

FONT = "Microsoft YaHei UI"

try:
    IS_WIN11 = sys.getwindowsversion().build >= 22000
except Exception:
    IS_WIN11 = False

# Segoe Fluent Icons / Segoe MDL2 Assets 的码位（两套字体一致）
GLYPH = {
    "refresh": "", "close": "", "mouse": "",
    "headset": "", "keyboard": "", "gamepad": "",
    "device": "", "rename": "", "delete": "",
    "settings": "", "info": "", "bolt": "",
}

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p,
                                         wt.UINT]
user32.SystemParametersInfoW.restype = wt.BOOL


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t + 0.5),
            int(a[1] + (b[1] - a[1]) * t + 0.5),
            int(a[2] + (b[2] - a[2]) * t + 0.5))


def _rgb(c):
    return "#%02x%02x%02x" % c


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in(t: float) -> float:
    return t * t * t


def ease_in_out(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def accent_rgb() -> tuple:
    """系统强调色（DWM AccentColor 是 0xAABBGGRR），读不到就用 Windows 默认蓝。"""
    v = reg_dword(r"Software\Microsoft\Windows\DWM", "AccentColor", 0)
    if not v:
        return (0, 120, 212)
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


def fluent_colors(light: bool) -> dict:
    acc = accent_rgb()
    if light:
        accent = _mix(acc, (0, 0, 0), 0.08)
        return {
            "bg": (243, 243, 245), "bg_top": _mix((249, 249, 251), acc, 0.04),
            "bg_bot": (240, 240, 242), "footer": (234, 234, 236),
            "divider": (222, 222, 224),
            "card": (252, 252, 252), "card_hover": (246, 246, 247),
            "stroke": (228, 228, 230), "stroke_top": (236, 236, 238),
            "stroke_bot": (212, 212, 215), "shadow": 0.06,
            "well": (238, 238, 240),
            "text": (26, 26, 26), "sub": (96, 96, 96), "dim": (138, 138, 138),
            "accent": accent, "accent_hover": _mix(accent, (255, 255, 255), 0.1),
            "on_accent": (255, 255, 255), "link": _mix(acc, (0, 0, 0), 0.2),
            "ok": (16, 137, 62), "warn": (202, 128, 0), "bad": (209, 52, 56),
            "track": (226, 226, 228),
            "ctl": (251, 251, 251), "ctl_hover": (246, 246, 246),
            "ctl_stroke": (214, 214, 214), "ctl_off": (245, 245, 245),
            "subtle_hover": (228, 228, 230),
            "menu": (249, 249, 249), "menu_hover": (234, 234, 234),
            "info": _mix((246, 246, 248), acc, 0.06),
        }
    accent = _mix(acc, (255, 255, 255), 0.45)
    return {
        "bg": (44, 44, 46), "bg_top": _mix((50, 50, 53), acc, 0.06),
        "bg_bot": (38, 38, 40), "footer": (32, 32, 33), "divider": (24, 24, 25),
        "card": (54, 54, 57), "card_hover": (63, 63, 66),
        "stroke": (34, 34, 36), "stroke_top": (76, 76, 80),
        "stroke_bot": (42, 42, 44), "shadow": 0.22,
        "well": (70, 70, 73),
        "text": (255, 255, 255), "sub": (204, 204, 206), "dim": (150, 150, 152),
        "accent": accent, "accent_hover": _mix(accent, (0, 0, 0), 0.1),
        "on_accent": (0, 0, 0), "link": accent,
        "ok": (108, 203, 95), "warn": (252, 200, 0), "bad": (255, 120, 130),
        "track": (76, 76, 80),
        "ctl": (60, 60, 62), "ctl_hover": (68, 68, 70),
        "ctl_stroke": (76, 76, 78), "ctl_off": (50, 50, 52),
        "subtle_hover": (62, 62, 65),
        "menu": (44, 44, 46), "menu_hover": (62, 62, 65),
        "info": _mix((50, 50, 54), acc, 0.10),
    }


def level_rgb(level: Optional[int], c: dict) -> tuple:
    if level is None:
        return c["dim"]
    if level >= 60:
        return c["ok"]
    if level >= 30:
        return c["warn"]
    return c["bad"]


def _device_glyph(name: str) -> str:
    n = (name or "").lower()
    for words, key in ((("mouse", "鼠标", "mice"), "mouse"),
                       (("headset", "耳机", "buds", "headphone", "audio"),
                        "headset"),
                       (("keyboard", "键盘"), "keyboard"),
                       (("gamepad", "controller", "手柄"), "gamepad")):
        if any(w in n for w in words):
            return GLYPH[key]
    return GLYPH["device"]


_ICON_FONT = None
_NUM_FONT = None


def icon_font(root) -> str:
    global _ICON_FONT
    if _ICON_FONT is None:
        fams = set(tkfont.families(root))
        _ICON_FONT = ("Segoe Fluent Icons" if "Segoe Fluent Icons" in fams
                      else "Segoe MDL2 Assets")
    return _ICON_FONT


def num_font(root) -> str:
    global _NUM_FONT
    if _NUM_FONT is None:
        fams = set(tkfont.families(root))
        _NUM_FONT = next((f for f in ("Segoe UI Variable Display",
                                      "Segoe UI Variable Text", "Segoe UI")
                          if f in fams), FONT)
    return _NUM_FONT


# -- 纯 Python 抗锯齿光栅：生成 RGBA PNG 给 Tk 的 PhotoImage ---------------
# 生成结果按 key 缓存成 base64；后台线程可提前填充，界面线程直接取用
_B64: dict = {}
_CLEAR = b"\0\0\0\0"


def _png_b64(w: int, h: int, px) -> str:
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += px[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 3))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def _cov(d: float) -> float:
    """有符号距离（负数在内）→ 像素覆盖率。"""
    if d >= 0.5:
        return 0.0
    if d <= -0.5:
        return 1.0
    return 0.5 - d


def _sd_rrect(x, y, cx, cy, hx, hy, r):
    qx = abs(x - cx) - (hx - r)
    qy = abs(y - cy) - (hy - r)
    return (math.hypot(qx if qx > 0 else 0.0, qy if qy > 0 else 0.0)
            + min(max(qx, qy), 0.0) - r)


def rrect_png(w: int, h: int, r: float, fill, stroke=None, sw: float = 1.0,
              stroke_bot=None) -> str:
    """圆角矩形；fill 为 None 只画描边；stroke_bot 给出时描边上亮下暗。
    只有四角和边缘逐像素计算，中间整段复用。"""
    r = max(0.5, min(r, w / 2.0, h / 2.0))
    c = w / 2.0
    m = h / 2.0
    edge = min(int(math.ceil(r + sw + 1.5)), w // 2, h // 2)
    inner = bytes((fill[0], fill[1], fill[2], 255)) if fill else _CLEAR

    def pix(fx, fy, st):
        d = _sd_rrect(fx, fy, c, m, c, m, r)
        a = _cov(d)
        if a <= 0.0:
            return _CLEAR
        if st is None:
            col, al = fill, a
        else:
            t = _cov(d + sw)
            if fill is None:
                col, al = st, a - t
            else:
                col, al = _mix(st, fill, t / a), a
        if al <= 0.0:
            return _CLEAR
        return bytes((col[0], col[1], col[2], int(al * 255 + 0.5)))

    out = bytearray()
    for y in range(h):
        fy = y + 0.5
        st = stroke
        if stroke is not None and stroke_bot is not None:
            st = _mix(stroke, stroke_bot, y / max(1, h - 1))
        if y < edge or y >= h - edge:
            out += b"".join(pix(x + 0.5, fy, st) for x in range(w))
        else:
            out += b"".join(pix(x + 0.5, fy, st) for x in range(edge))
            out += inner * (w - 2 * edge)
            out += b"".join(pix(x + 0.5, fy, st) for x in range(w - edge, w))
    return _png_b64(w, h, out)


def shadow_png(w: int, h: int, r: float, blur: int, alpha: float) -> str:
    """卡片投影：图像比卡片四周各大 blur 像素，高斯衰减。"""
    W, H = w + 2 * blur, h + 2 * blur
    cx, cy = W / 2.0, H / 2.0
    hx, hy = w / 2.0, h / 2.0
    k = 1.0 / (2.0 * (blur / 2.4) ** 2)
    edge = min(int(blur + r + 2), W // 2, H // 2)
    inner = bytes((0, 0, 0, int(alpha * 255 + 0.5)))

    def pix(fx, fy):
        d = _sd_rrect(fx, fy, cx, cy, hx, hy, r)
        a = alpha if d <= 0 else alpha * math.exp(-d * d * k)
        ai = int(a * 255 + 0.5)
        return bytes((0, 0, 0, ai)) if ai else _CLEAR

    out = bytearray()
    mid = None
    for y in range(H):
        fy = y + 0.5
        if y < edge or y >= H - edge:
            out += b"".join(pix(x + 0.5, fy) for x in range(W))
        else:
            if mid is None:
                mid = (b"".join(pix(x + 0.5, fy) for x in range(edge))
                       + inner * (W - 2 * edge)
                       + b"".join(pix(x + 0.5, fy)
                                  for x in range(W - edge, W)))
            out += mid
    return _png_b64(W, H, out)


def vgrad_png(w: int, h: int, top, bot, y0: int, total: int) -> str:
    """竖直渐变的一段（y0 起 h 行，整段高 total），便于分块遮罩拼接。"""
    out = bytearray()
    for y in range(h):
        c = _mix(top, bot, min(1.0, (y0 + y) / max(1, total - 1)))
        out += bytes((c[0], c[1], c[2], 255)) * w
    return _png_b64(w, h, out)


def blank_png(w: int, h: int) -> str:
    return _png_b64(w, h, bytes(w * h * 4))


def _composite(w: int, h: int, layers) -> str:
    """layers = [(sdf(x, y), rgb)]，自下而上做 over 合成。"""
    px = bytearray(w * h * 4)
    for y in range(h):
        fy = y + 0.5
        for x in range(w):
            fx = x + 0.5
            r = g = b = a = 0.0
            for sd, col in layers:
                k = _cov(sd(fx, fy))
                if k <= 0.0:
                    continue
                r = col[0] * k + r * (1 - k)
                g = col[1] * k + g * (1 - k)
                b = col[2] * k + b * (1 - k)
                a = k + a * (1 - k)
            if a <= 0.0:
                continue
            i = (y * w + x) * 4
            px[i] = min(255, int(r / a + 0.5))
            px[i + 1] = min(255, int(g / a + 0.5))
            px[i + 2] = min(255, int(b / a + 0.5))
            px[i + 3] = int(a * 255 + 0.5)
    return _png_b64(w, h, px)


def disc_png(size: int, color) -> str:
    c = size / 2.0
    return _composite(size, size,
                      [(lambda x, y: math.hypot(x - c, y - c) - c, color)])


_RING_GEOM: dict = {}


def _ring_geom(size: int, thick: float):
    """圆环覆盖到的像素只算一次：(偏移, 覆盖率, 角度, x, y)。"""
    key = (size, round(thick, 3))
    g = _RING_GEOM.get(key)
    if g is None:
        c = size / 2.0
        rm = (size - thick) / 2.0
        hw = thick / 2.0
        pts = []
        for y in range(size):
            fy = y + 0.5
            for x in range(size):
                fx = x + 0.5
                dx, dy = fx - c, fy - c
                tc = _cov(abs(math.hypot(dx, dy) - rm) - hw)
                if tc > 0.0:
                    pts.append(((y * size + x) * 4, tc,
                                math.atan2(dx, -dy) % (2 * math.pi), fx, fy))
        g = (pts, c, rm, hw)
        _RING_GEOM[key] = g
    return g


def ring_png(size: int, thick: float, level: Optional[int], color, track,
             head=None) -> str:
    """电量圆环：12 点钟起顺时针，圆头端点；head 给出时尾部颜色渐浅。"""
    pts, c, rm, hw = _ring_geom(size, thick)
    px = bytearray(size * size * 4)
    lv = 0 if level is None else max(0, min(100, int(level)))
    sweep = 2 * math.pi * lv / 100.0
    sx, sy = c, c - rm
    ex, ey = c + rm * math.sin(sweep), c - rm * math.cos(sweep)
    tail = head or color
    for i, tc, ang, fx, fy in pts:
        if lv >= 100:
            pc, frac = tc, ang / (2 * math.pi)
        elif lv <= 0:
            pc, frac = 0.0, 1.0
        elif ang <= sweep:
            pc, frac = tc, ang / sweep
        else:
            cs = _cov(math.hypot(fx - sx, fy - sy) - hw)
            ce = _cov(math.hypot(fx - ex, fy - ey) - hw)
            pc, frac = (ce, 1.0) if ce >= cs else (cs, 0.0)
        col = _mix(tail, color, frac) if pc > 0.0 else color
        a = pc + tc * (1 - pc)
        q = tc * (1 - pc)
        px[i] = min(255, int((col[0] * pc + track[0] * q) / a + 0.5))
        px[i + 1] = min(255, int((col[1] * pc + track[1] * q) / a + 0.5))
        px[i + 2] = min(255, int((col[2] * pc + track[2] * q) / a + 0.5))
        px[i + 3] = int(a * 255 + 0.5)
    return _png_b64(size, size, px)


def toggle_png(w: int, h: int, s: float, t: float, hover: bool,
               c: dict) -> str:
    """开关：t 从 0（关）到 1（开），滑块位置和颜色一起过渡。"""
    hx, hy, r = w / 2.0, h / 2.0, h / 2.0
    inset = max(1.0, s)
    acc = c["accent_hover"] if hover else c["accent"]
    off_fill = c["ctl_hover"] if hover else c["ctl_off"]
    knob = (7.0 if hover else 6.0) * s
    kx = r + (w - 2 * r) * t
    layers = [
        (lambda x, y: _sd_rrect(x, y, hx, hy, hx, hy, r),
         _mix(c["sub"], acc, t)),
        (lambda x, y: _sd_rrect(x, y, hx, hy, hx - inset, hy - inset,
                                r - inset), _mix(off_fill, acc, t)),
        (lambda x, y: math.hypot(x - kx, y - hy) - knob,
         _mix(c["sub"], c["on_accent"], t)),
    ]
    return _composite(w, h, layers)


# -- 窗口外观 -----------------------------------------------------------------
def set_round_region(hwnd: int, w: int, h: int, radius: int = 24) -> None:
    try:
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
        user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


def toplevel_hwnd(win) -> int:
    child = win.winfo_id()
    try:
        return user32.GetAncestor(child, 2) or child
    except Exception:
        return child


def apply_fluent_frame(win, w: int, h: int, radius: int, border) -> None:
    """Win11：DWM 原生圆角 + 描边 + 阴影；Win10：窗口区域裁圆角。"""
    hwnd = toplevel_hwnd(win)
    if IS_WIN11:
        try:
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            col = wt.DWORD(COLORREF(*border))
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), 4)
            GCL_STYLE = -26
            style = user32.GetClassLongPtrW(hwnd, GCL_STYLE)
            if not style & 0x00020000:  # CS_DROPSHADOW
                user32.SetClassLongPtrW(hwnd, GCL_STYLE, style | 0x00020000)
            return
        except Exception:
            pass
    set_round_region(hwnd, w, h, radius * 2)


def work_area() -> tuple:
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    rc = wt.RECT()
    try:
        if user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0):
            if rc.right > rc.left and rc.bottom > rc.top:
                return rc.left, rc.top, rc.right, rc.bottom, sw, sh
    except Exception:
        pass
    return 0, 0, sw, sh, sw, sh


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wt.BYTE), ("BlendFlags", wt.BYTE),
                ("SourceConstantAlpha", wt.BYTE), ("AlphaFormat", wt.BYTE)]


user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]


class ShadowWindow:
    """分层窗口：在面板背后画一圈向外渐隐的灰色光晕（Win10 用）。"""

    def __init__(self, panel):
        self.panel = panel
        self.hwnd = 0
        self._bitmap = None
        self._create()

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        # TOPMOST：边框层要能压在另一个同款窗口之上
        ex = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080 | 0x00000008
        self.hwnd = user32.CreateWindowExW(
            ex, "STATIC", "", 0x80000000, 0, 0, 10, 10,
            None, None, hinst, None)

    def _build(self):
        W, H, S = self.panel.W, self.panel.H, self.panel.S
        margin = max(24, int(28 * S))
        w, h = W + 2 * margin, H + 2 * margin
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        memdc = gdi32.CreateCompatibleDC(None)
        hbm = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        gdi32.SelectObject(memdc, hbm)
        buf = bytearray(w * h * 4)
        cx, cy = margin + W / 2.0, margin + H / 2.0
        r = 16.0 * S
        hx, hy = W / 2.0 - r, H / 2.0 - r
        sigma = 6.0 * S
        kw = 1.0 / (2.0 * sigma * sigma)
        limit = 3.0 * sigma
        for yy in range(h):
            py = yy + 0.5
            qy = abs(py - cy) - hy
            if qy > limit + r:
                continue
            oy = qy if qy > 0 else 0.0
            for xx in range(w):
                px = xx + 0.5
                qx = abs(px - cx) - hx
                if qx > limit + r:
                    continue
                ox = qx if qx > 0 else 0.0
                d = math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r
                i = (yy * w + xx) * 4
                if -3.0 < d <= 3.0:
                    cov = 1.0 if d <= 1.8 else max(0.0, (3.0 - d) / 1.2)
                    a = int(255 * cov)
                    buf[i] = 60 * a // 255
                    buf[i + 1] = 60 * a // 255
                    buf[i + 2] = 60 * a // 255
                    buf[i + 3] = a
                elif 3.0 < d < limit:
                    a = int(40.0 * math.exp(-(d * d) * kw))
                    if a > 0:
                        buf[i + 3] = a
        ctypes.memmove(bits, bytes(buf), len(buf))
        self._bitmap = (w, h, memdc, hbm, margin)

    def _top(self):
        return user32.GetAncestor(self.panel.hwnd, 2) or self.panel.hwnd

    def show(self, x, y):
        if not self.hwnd:
            return
        if not getattr(self.panel, "_visible", True):
            return
        if self._bitmap is None:
            self._build()
        w, h, memdc, _hbm, margin = self._bitmap
        pt_dst = wt.POINT(int(x) - margin, int(y) - margin)
        size = wt.SIZE(w, h)
        pt_src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst),
                                   ctypes.byref(size), memdc,
                                   ctypes.byref(pt_src), 0,
                                   ctypes.byref(blend), 2)
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h,
                            0x0010 | 0x0040)

    def move(self, x, y):
        if not self.hwnd or self._bitmap is None:
            return
        w, h, _memdc, _hbm, margin = self._bitmap
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h, 0x0010)

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0
        if self._bitmap:
            _w, _h, memdc, hbm, _m = self._bitmap
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(memdc)
            self._bitmap = None


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong),
        ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]



class _Fluent:
    """Fluent 窗口的公共部分：配色、字体、图像缓存、按钮、动画与拖动。"""

    def _fl_init(self, light: bool):
        self.S = max(1.0, UI_SCALE)
        self.light = light
        self.c = fluent_colors(light)
        self._imgs = {}
        self._btns = {}
        self._drag = None
        self._anims = []
        self._tick_list = []
        self._anim_on = False

    def _set_theme(self, light: bool):
        if light != self.light:
            self.light = light
            self.c = fluent_colors(light)
            self._imgs.clear()

    def _px(self, v) -> int:
        return max(1, int(round(v * self.S)))

    def _f(self, size, bold=False, family=None):
        return (family or FONT, -int(round(size * self.S)),
                "bold" if bold else "normal")

    def _fi(self, size):
        return (icon_font(self.root), -int(round(size * self.S)))

    def _fn(self, size, bold=True):
        return self._f(size, bold, num_font(self.root))

    # -- 图像 ----------------------------------------------------------------
    def _image(self, key, make):
        img = self._imgs.get(key)
        if img is None:
            data = _B64.get(key)
            if data is None:
                data = make()
                _B64[key] = data
            img = tk.PhotoImage(master=self.root, data=data, format="png")
            self._imgs[key] = img
        return img

    def _img(self, spec):
        return self._image(*spec)

    def _spec_rr(self, w, h, r, fill, stroke=None, stroke_bot=None):
        W, H, R = self._px(w), self._px(h), round(r * self.S, 3)
        sw = round(max(1.0, self.S), 3)
        return (("rr", W, H, R, fill, stroke, sw, stroke_bot),
                lambda: rrect_png(W, H, R, fill, stroke, sw, stroke_bot))

    def _rr(self, w, h, r, fill, stroke=None, stroke_bot=None):
        return self._img(self._spec_rr(w, h, r, fill, stroke, stroke_bot))

    def _spec_shadow(self, w, h, r):
        W, H, R = self._px(w), self._px(h), round(r * self.S, 3)
        B, A = self._px(10), self.c["shadow"]
        return (("shadow", W, H, R, B, A),
                lambda: shadow_png(W, H, R, B, A))

    def _disc(self, size, color):
        D = self._px(size)
        return self._image(("disc", D, color), lambda: disc_png(D, color))

    def _spec_ring(self, size, thick, level, color):
        D, T = self._px(size), round(thick * self.S, 3)
        track = self.c["track"]
        head = _mix(color, (255, 255, 255), 0.38)
        return (("ring", D, T, level, color, track),
                lambda: ring_png(D, T, level, color, track, head))

    def _ring(self, size, thick, level, color):
        return self._img(self._spec_ring(size, thick, level, color))

    def _spec_toggle(self, frame, hover):
        W, H, S, c = self._px(40), self._px(20), self.S, self.c
        key = ("tog", W, H, frame, hover, c["accent"], c["sub"], c["ctl_off"],
               c["on_accent"])
        return key, lambda: toggle_png(W, H, S, frame / 6.0, hover, c)

    def _blank(self, w, h):
        W, H = self._px(w), self._px(h)
        return self._image(("blank", W, H), lambda: blank_png(W, H))

    def _grad(self, w, y0, h, total):
        """背景渐变的一段，用来铺底和做滚动遮罩，接缝处颜色连续。"""
        W, Y0, Hh, T = (self._px(w), int(round(y0 * self.S)), self._px(h),
                        self._px(total))
        top, bot = self.c["bg_top"], self.c["bg_bot"]
        return self._image(("grad", W, Y0, Hh, T, top, bot),
                           lambda: vgrad_png(W, Hh, top, bot, Y0, T))

    # -- 动画引擎 ------------------------------------------------------------
    def _anim(self, dur, step, delay=0.0, done=None, key=None,
              ease=ease_out):
        """step(e) 每帧收到缓动后的进度 0→1；同 key 的旧动画会被替换。"""
        if key:
            self._cancel(key, exact=True)
        self._anims.append([time.perf_counter() + delay, max(0.001, dur),
                            step, done, key, ease, False])
        if not self._anim_on:
            self._anim_on = True
            try:
                self.root.after(0, self._anim_tick)
            except tk.TclError:
                self._anim_on = False

    def _cancel(self, prefix, exact=False):
        for lst in (self._anims, self._tick_list):
            for a in lst:
                k = a[4]
                if k and (k == prefix if exact else k.startswith(prefix)):
                    a[6] = True
        self._anims = [a for a in self._anims if not a[6]]

    def _anim_tick(self):
        self._tick_list, self._anims = self._anims, []
        now = time.perf_counter()
        keep = []
        for a in self._tick_list:
            if a[6]:
                continue
            start, dur, step, done, _key, ease, _dead = a
            if now < start:
                keep.append(a)
                continue
            t = min(1.0, (now - start) / dur)
            try:
                step(ease(t))
            except Exception:
                a[6] = True
                continue
            if t < 1.0:
                keep.append(a)
                continue
            a[6] = True
            if done:
                try:
                    done()
                except Exception:
                    pass
        self._tick_list = []
        self._anims = [a for a in keep if not a[6]] + self._anims
        if self._anims:
            try:
                self.root.after(15, self._anim_tick)
                return
            except tk.TclError:
                pass
        self._anim_on = False

    def _open_anim(self, x, y, dy=14, dur=0.24):
        """对话框出现：淡入 + 上浮。"""
        def step(e):
            self.root.geometry(f"+{x}+{int(y + dy * self.S * (1 - e))}")
            self.root.attributes("-alpha", e)

        def done():
            sh = getattr(self, "shadow", None)
            if sh:
                sh.show(x, y)
        self._anim(dur, step, key="win", done=done)

    def _close_anim(self, done, dur=0.11):
        sh = getattr(self, "shadow", None)
        if sh:
            try:
                sh.hide()
            except Exception:
                pass
        self._anim(dur, lambda e: self.root.attributes("-alpha", 1 - e),
                   key="win", done=done, ease=ease_in)

    # -- 文字与控件 ----------------------------------------------------------
    def _fit(self, text, font, max_w) -> str:
        """按逻辑像素宽度截断，超出加省略号。"""
        f = tkfont.Font(root=self.root, font=font)
        limit = max_w * self.S
        if f.measure(text) <= limit:
            return text
        while text and f.measure(text + "…") > limit:
            text = text[:-1]
        return text + "…"

    def _ctext(self, x, y, text, fill, font, tags=(), anchor="center",
               **kw):
        """按文字实际包围盒做垂直光学居中。"""
        item = self.cv.create_text(x, y, text=text, fill=_rgb(fill),
                                   font=font, tags=tags, anchor=anchor, **kw)
        try:
            _x1, y1, _x2, y2 = self.cv.bbox(item)
            dy = (y1 + y2) / 2.0 - y
            if abs(dy) >= 0.5:
                self.cv.move(item, 0, -dy)
        except Exception:
            pass
        return item

    def _button(self, x, y, w, h, text, cmd, kind="standard",
                enabled=True, tag=None, glyph=False, size=None):
        """kind：accent / standard / subtle / close；悬停是 4 帧淡入。"""
        c = self.c
        cv = self.cv
        tag = tag or f"btn_{text}_{int(x)}_{int(y)}"
        radius = 0.5 if kind == "close" else 4
        if not enabled:
            fills = [c["ctl_off"]] * 2
            stroke, col, hcol = c["ctl_stroke"], c["dim"], c["dim"]
        elif kind == "accent":
            fills = [c["accent"], c["accent_hover"]]
            stroke, col, hcol = None, c["on_accent"], c["on_accent"]
        elif kind == "subtle":
            fills = [None, c["subtle_hover"]]
            stroke, col, hcol = None, c["sub"], c["text"]
        elif kind == "close":
            fills = [None, (196, 43, 28)]
            stroke, col, hcol = None, c["sub"], (255, 255, 255)
        else:
            fills = [c["ctl"], c["ctl_hover"]]
            stroke, col, hcol = c["ctl_stroke"], c["text"], c["text"]
        # 命中区：透明图像（Tk 按包围盒命中），不会在渐变背景上露出色块
        cv.create_image(x, y, image=self._blank(w, h), anchor="nw",
                        tags=(tag,))
        frames = []
        for k in range(4):
            t = k / 3.0
            if fills[0] is None:
                frames.append(self._rr(w, h, radius, fills[1]) if k else None)
            else:
                frames.append(self._rr(w, h, radius,
                                       _mix(fills[0], fills[1], t), stroke))
        bg = cv.create_image(x, y, anchor="nw", tags=(tag,),
                             image=frames[0] or self._blank(w, h))
        font = (self._fi(size or 12) if glyph
                else self._f(size or 10, kind == "accent"))
        txt = self._ctext(x + w / 2, y + h / 2, text, col, font, tags=(tag,))
        self._btns[tag] = (bg, frames, txt, col, hcol, [0.0])
        if enabled:
            cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
            cv.tag_bind(tag, "<Enter>", lambda e: self._hover_btn(tag, True))
            cv.tag_bind(tag, "<Leave>", lambda e: self._hover_btn(tag, False))
        return tag

    def _hover_btn(self, tag, on):
        info = self._btns.get(tag)
        if not info:
            return
        bg, frames, txt, col, hcol, cur = info
        t0, t1 = cur[0], 1.0 if on else 0.0

        def step(e):
            t = t0 + (t1 - t0) * e
            cur[0] = t
            img = frames[int(round(t * 3))]
            self.cv.itemconfigure(bg, image=img or self._blank(1, 1))
            self.cv.itemconfigure(txt, fill=_rgb(_mix(col, hcol, t)))
        self._anim(0.1, step, key=f"hv_{tag}", ease=lambda t: t)

    def _link(self, x, y, text, cmd, tag, size=10, anchor="w"):
        c = self.c
        item = self._ctext(x, y, text, c["link"], self._f(size), tags=(tag,),
                           anchor=anchor)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        self.cv.tag_bind(tag, "<Enter>", lambda e: (
            self.cv.configure(cursor="hand2"),
            self.cv.itemconfigure(item, font=self._f(size) + ("underline",))))
        self.cv.tag_bind(tag, "<Leave>", lambda e: (
            self.cv.configure(cursor=""),
            self.cv.itemconfigure(item, font=self._f(size))))
        return item

    def _center_on_work_area(self):
        l, t, r, b, _sw, _sh = work_area()
        x = l + max(0, (r - l - self.W) // 2)
        y = t + max(0, (b - t - self.H) // 2)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        return x, y

    def _drag_press(self, e, head_h, skip=()):
        self._drag = None
        if e.y >= head_h * self.S:
            return
        tags = self.cv.gettags("current")
        if any(t.startswith(skip) for t in tags):
            return
        self._drag = (e.x_root, e.y_root,
                      self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        nx, ny = wx + e.x_root - x0, wy + e.y_root - y0
        self.root.geometry(f"+{nx}+{ny}")
        sh = getattr(self, "shadow", None)
        if sh:
            try:
                sh.move(nx, ny)
            except Exception:
                pass

    def _new_window(self, master, title, light, w, h, bg_key="bg"):
        """无边框 Toplevel + Canvas，先全透明，等动画淡入。"""
        self.root = tk.Toplevel(master)
        self.root.title(title)
        self.root.overrideredirect(True)
        self._fl_init(light)
        self.W = self._px(w)
        self.H = self._px(h)
        bg = _rgb(self.c[bg_key])
        self.root.configure(bg=bg)
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0, bg=bg)
        self.cv.pack(fill="both", expand=True)


class HelpWindow(_Fluent):
    """读取设置：说明 + 抓包学习协议。"""

    LW, LH = 480, 572
    STEPS = [
        "安装 USBPcap（装完重启电脑）。",
        "关闭厂家驱动，点「开始抓包」并允许管理员权限。",
        "打开厂家驱动，让它显示一次电量。",
        "点「停止」，填入刚才看到的电量。",
        "只有一个字节符合，就直接保存这个字节作为电量。",
        "多个字节符合时，换一个电量再抓一次，对比差值。",
        "保存后，以后插上就能直接读电量。",
    ]

    def __init__(self, parent, key=None):
        self.parent = parent
        self.key = key
        r = parent.app.snapshot().get(key) if key else None
        self.dev_name = (r.name if r and r.name else "")
        self.target_vid = r.vid if r else 0
        self.target_pid = r.pid if r else 0
        self._new_window(parent.root, "读取设置", parent.app.theme_light,
                         self.LW, self.LH)
        self.cv.bind("<Button-1>", lambda e: self._drag_press(
            e, 64, ("help_", "btn_", "lnk_")))
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>",
                     lambda e: setattr(self, "_drag", None))
        self.root.bind("<Escape>", lambda e: self.close(animate=True))
        x, y = self._center_on_work_area()
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                           self.c["stroke"])
        self.shadow = None
        if not IS_WIN11:
            try:
                self.shadow = ShadowWindow(self)
            except Exception:
                self.shadow = None
        self._closing = False
        self._capturing = False
        self._cap_file = None
        self._cap_proc = None
        self._cap_dir = ""
        self._cap_status_file = ""
        self._cap_stop = ""
        self._cap_status = "点击「开始抓包」，抓包时请打开厂家软件显示电量"
        self._cap_txt = ""
        self._ambiguous = False
        self._cap_a = None
        self._purge_capture_files()
        self._first = True
        self.render()
        self._open_anim(x, y)
        try:
            self.root.focus_force()
        except Exception:
            pass

    def render(self):
        c = self.c
        cv = self.cv
        self._cancel("hv_")
        cv.delete("all")
        self._btns = {}
        W, H = self.LW, self.LH
        cv.create_image(0, 0, anchor="nw", image=self._grad(W, 0, H, H))
        if not IS_WIN11:
            cv.create_image(0, 0, image=self._rr(W, H, 8, None, c["ctl_stroke"]),
                            anchor="nw")
        self._ctext(24, 30, "读取设置", c["text"], self._f(15, True),
                    anchor="w")
        sub = "协议未知 · 让软件学会读取这款设备的电量"
        if self.dev_name:
            sub = f"{self.dev_name} · 协议未知"
        self._ctext(24, 54, self._fit(sub, self._f(10), W - 90), c["sub"],
                    self._f(10), anchor="w")
        self._button(W - 46, 0, 46, 32, GLYPH["close"],
                     lambda: self.close(animate=True), kind="close",
                     tag="help_close", glyph=True, size=10)

        # 说明卡片：步骤逐条浮现（只在第一次绘制时）
        top, bot = 78, 424
        cv.create_image(16 - 10, top - 8, anchor="nw",
                        image=self._img(self._spec_shadow(W - 32, bot - top,
                                                          6)))
        cv.create_image(16, top, anchor="nw",
                        image=self._rr(W - 32, bot - top, 6, c["card"],
                                       c["stroke_top"], c["stroke_bot"]))
        self._ctext(32, top + 22, "为什么读不到", c["text"],
                    self._f(11, True), anchor="w")
        self._ctext(32, top + 44, "接收器在，但厂商协议未收录；软件只做只读试探。",
                    c["sub"], self._f(10), anchor="w")
        self._ctext(32, top + 78, "操作步骤", c["text"], self._f(11, True),
                    anchor="w")
        y = top + 104
        steps = []
        for i, text in enumerate(self.STEPS, 1):
            tag = f"step_{i}"
            cv.create_image(32, y - 10, anchor="nw", tags=(tag,),
                            image=self._disc(20, _mix(c["card"], c["accent"],
                                                      0.22)))
            a = self._ctext(42, y, str(i), c["link"], self._fn(9), tags=(tag,))
            b = self._ctext(62, y, text, c["sub"], self._f(10), anchor="w",
                            tags=(tag,))
            steps.append((tag, [(a, c["link"]), (b, c["sub"])]))
            y += 28
        self._ctext(32, y + 8, "如果多次都没有电量数据", c["text"],
                    self._f(11, True), anchor="w")
        self._ctext(32, y + 30, "可能厂家的协议做了加密，这方面需要用户自行解决。",
                    c["sub"], self._f(10), anchor="w")

        # 操作区
        by = 440
        x = 16
        self._button(x, by, 112, 32, "开始抓包", lambda: self._cap_click(
            "cap_start"), kind="accent", enabled=not self._capturing,
            tag="btn_cap_start")
        x += 120
        self._button(x, by, 80, 32, "停止", lambda: self._cap_click(
            "cap_stop"), enabled=self._capturing, tag="btn_cap_stop")
        x += 88
        if self._ambiguous:
            self._button(x, by, 96, 32, "清理抓包", lambda: self._cap_click(
                "cap_clear"), enabled=not self._capturing,
                tag="btn_cap_clear")
        self._link(W - 24, by + 8, "下载 USBPcap", self._open_site,
                   "lnk_site", size=9, anchor="e")
        self._link(W - 24, by + 25, "指定 USBPcapCMD 路径",
                   lambda: self._cap_click("cap_path"), "lnk_path", size=9,
                   anchor="e")

        # 状态信息条；抓包进行中图标呼吸
        iy = 488
        cv.create_image(16, iy, anchor="nw",
                        image=self._rr(W - 32, 64, 6, c["info"],
                                       c["stroke_top"], c["stroke_bot"]))
        icon = self._ctext(36, iy + 20, GLYPH["info"], c["link"],
                           self._fi(14))
        cv.create_text(58, iy + 12, text=self._cap_status, anchor="nw",
                       fill=_rgb(c["text"]), font=self._f(10),
                       width=(W - 32 - 58) * self.S, justify="left")
        cv.scale("all", 0, 0, self.S, self.S)
        if self._capturing:
            self._breathe(icon)
        if self._first:
            self._first = False
            S = self.S
            for i, (tag, texts) in enumerate(steps):
                cv.move(tag, 0, 8 * S)
                for item, _col in texts:
                    cv.itemconfigure(item, fill=_rgb(c["card"]))

                def step(e, tag=tag, texts=texts, moved=[0.0]):
                    off = 8 * S * e
                    cv.move(tag, 0, -(off - moved[0]))
                    moved[0] = off
                    for item, col in texts:
                        cv.itemconfigure(item, fill=_rgb(_mix(c["card"], col,
                                                               e)))
                self._anim(0.3, step, delay=0.12 + i * 0.04, key=f"st_{i}")

    def _breathe(self, item):
        c = self.c

        def step(e):
            k = (math.sin(time.perf_counter() * 4) + 1) / 2
            self.cv.itemconfigure(item, fill=_rgb(_mix(c["dim"], c["link"],
                                                        k)))
        self._anim(3600, step, key="breathe", ease=lambda t: t)

    def _cap_click(self, tag):
        if tag == "cap_start":
            self.start_capture()
        elif tag == "cap_stop":
            self.stop_capture()
        elif tag == "cap_path":
            self._choose_usbpcap()
        elif tag == "cap_clear":
            self._clear_capture()

    def _clear_capture(self):
        self._capturing = False
        try:
            if self._cap_stop:
                with open(self._cap_stop, "w", encoding="utf-8") as f:
                    f.write("1")
        except Exception:
            pass
        if self._cap_proc:
            try:
                kernel32.TerminateProcess(self._cap_proc, 0)
                kernel32.CloseHandle(self._cap_proc)
            except Exception:
                pass
            self._cap_proc = None
        for p in (self._cap_file, getattr(self, "_cap_txt", ""),
                  self._cap_status_file, self._cap_stop):
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        outdir = self._cap_dir or os.path.join(
            tempfile.gettempdir(), "BatteryTrayCapture")
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass
        self._cap_file = None
        self._cap_txt = ""
        self._ambiguous = False
        self._cap_a = None
        self._cap_status = "已清理抓包，可以重新开始"
        self.render()

    def _find_usbpcap(self):
        p = usbpcap_path()
        if p and os.path.isfile(p):
            return p
        for c in (r"C:\Program Files\USBPcap\USBPcapCMD.exe",
                  r"C:\Program Files (x86)\USBPcap\USBPcapCMD.exe"):
            if os.path.isfile(c):
                set_usbpcap_path(c)
                return c
        return ""

    def _path_status(self):
        p = usbpcap_path()
        if not p:
            return "未选择 USBPcap 路径"
        shown = p if len(p) <= 56 else "…" + p[-55:]
        return f"USBPcap：{shown}"

    def _choose_usbpcap(self):
        try:
            p = filedialog.askopenfilename(
                parent=self.root, title="选择 USBPcapCMD.exe",
                filetypes=[("USBPcapCMD.exe", "USBPcapCMD.exe"),
                           ("可执行文件", "*.exe")])
        except Exception:
            p = ""
        if p:
            set_usbpcap_path(p)
            self._cap_status = self._path_status()
            self.render()

    def _open_site(self):
        try:
            webbrowser.open("https://desowin.org/usbpcap/")
        except Exception:
            pass

    def start_capture(self):
        if self._capturing:
            return
        cmd = self._find_usbpcap()
        if not cmd:
            self._cap_status = "未找到 USBPcapCMD.exe，请先安装 USBPcap"
            self.render()
            return
        outdir = os.path.join(tempfile.gettempdir(), "BatteryTrayCapture")
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception:
            pass
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass
        status_file = os.path.join(outdir, "status.json")
        stop_file = os.path.join(outdir, "stop.flag")
        if getattr(sys, "frozen", False):
            params = (f'--capture-helper "{cmd}" {self.target_vid:04X} '
                      f'{self.target_pid:04X} "{outdir}" "{stop_file}" '
                      f'"{status_file}"')
        else:
            script = os.path.abspath(__file__)
            params = (f'"{script}" --capture-helper "{cmd}" '
                      f'{self.target_vid:04X} {self.target_pid:04X} '
                      f'"{outdir}" "{stop_file}" "{status_file}"')
        sei = _SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = sys.executable
        sei.lpParameters = params
        sei.lpDirectory = outdir
        sei.nShow = 0
        try:
            ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        except Exception:
            ok = 0
        if not ok:
            self._cap_status = "启动失败（可能取消了管理员授权）"
            self.render()
            return
        self._cap_proc = sei.hProcess
        self._cap_dir = outdir
        self._cap_status_file = status_file
        self._cap_stop = stop_file
        self._cap_file = os.path.join(outdir, "capture.pcap")
        self._capturing = True
        self._cap_status = ("正在查找设备所在的 USBPcap 接口…"
                            if self.target_vid else "正在抓包…")
        self.render()
        self.root.after(400, self._poll_capture)

    def _poll_capture(self):
        if not self._capturing:
            return
        state = ""
        hub = 0
        path = ""
        try:
            with open(self._cap_status_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            state = info.get("state", "")
            hub = int(info.get("hub", 0) or 0)
            path = info.get("file", "")
        except Exception:
            pass
        if state == "capturing":
            self._cap_status = f"正在抓包：USBPcap{hub}"
            self.render()
        elif state == "busy":
            self._capturing = False
            self._cap_status = "抓包设备打不开（可能被其它抓包占用，或需要管理员权限）"
            self.render()
            return
        elif state == "notraffic":
            self._capturing = False
            self._cap_status = "抓到了流量但没发现目标设备，请在抓包时拔插一次接收器"
            self.render()
            return
        elif state == "notfound":
            self._capturing = False
            self._cap_status = "没有在任何 USBPcap 接口上找到这台设备"
            self.render()
            return
        elif state == "error":
            self._capturing = False
            self._cap_status = "USBPcap 启动失败"
            self.render()
            return
        elif state == "done":
            self._capturing = False
            if path and os.path.isfile(path):
                self._cap_file = path
                self._finish_capture()
            else:
                self._cap_status = "抓包结束，但没有生成文件"
                self.render()
            return
        self.root.after(400, self._poll_capture)

    def stop_capture(self):
        if not self._capturing:
            return
        try:
            with open(self._cap_stop, "w", encoding="utf-8") as f:
                f.write("1")
        except Exception:
            pass
        self._cap_status = "正在停止…"
        self.render()

    def _finish_capture(self):
        if not self._cap_file:
            self._cap_status = "抓包结束，但没有生成文件"
            self.render()
            return
        try:
            size = os.path.getsize(self._cap_file)
            detail = f"（{max(1, size // 1024)} KB）"
        except Exception:
            detail = "（没有生成文件）"
        if self.target_vid:
            frames, _mapping = extract_pcap_for(
                self._cap_file, self.target_vid, self.target_pid)
        else:
            frames = extract_pcap_reports(self._cap_file)
        self._cap_txt = ""
        if frames:
            txt = os.path.splitext(self._cap_file)[0] + ".txt"
            try:
                with open(txt, "w", encoding="utf-8") as f:
                    f.write(format_reports(frames))
                self._cap_txt = txt
            except Exception:
                self._cap_txt = ""
        devs = scan_pcap_devices(self._cap_file)
        if self.target_vid:
            devs = [d for d in devs
                    if d == (self.target_vid, self.target_pid)]
        if not devs and self.target_vid:
            self._ambiguous = False
            self._cap_status = "抓包里没有这台设备，请确认接收器已插好"
            self.render()
            return
        if frames and len(devs) == 1:
            try:
                lv = simpledialog.askinteger(
                    "抓包电量",
                    "这次抓包时，厂商软件显示的电量是多少？",
                    parent=self.root, minvalue=0, maxvalue=100)
            except Exception:
                lv = None
            if lv is None:
                self._cap_status = "已停止，但没有填写电量，未记录本次抓包"
                self.render()
                return
            second = self._cap_a is not None
            if second:
                lv_a, frames_a = self._cap_a
                if int(lv) == int(lv_a):
                    self._ambiguous = True
                    self._cap_status = "两次电量相同，请换一个相差至少 10% 的电量再抓一次"
                    self.render()
                    return
                proto, why = infer_two(lv_a, frames_a, int(lv), frames)
            else:
                proto, why = infer_single_ex(int(lv), frames)

            if proto:
                vid, pid = devs[0]
                mode, verified = _validate_capture_protocol(
                    proto, vid, pid, int(lv))
                self._ambiguous = False
                self._cap_a = None
                if mode:
                    saved = dict(proto)
                    if mode == "invert":
                        saved["invert"] = True
                    save_user_protocol(vid, pid, saved)
                    forget_last(vid, pid)
                    if mode == "invert":
                        self._cap_status = (
                            f"已保存反向协议 {vid:04X}:{pid:04X}"
                            f"（回读 {verified.level}% → "
                            f"{100 - verified.level}%）")
                    else:
                        self._cap_status = (
                            f"已保存协议 {vid:04X}:{pid:04X}"
                            f"（回读 {verified.level}%）")
                else:
                    self._cap_status = (
                        "抓包中不含电量数据，可能是没按要求让厂商软件发电量请求")
                try:
                    self.parent.app.refresh()
                except Exception:
                    pass
                self.render()
                return

            if why == "ambiguous":
                self._cap_a = (int(lv), frames)
                self._ambiguous = True
                if second:
                    self._cap_status = "两次仍匹配到多个字节，请再换一个电量抓取"
                else:
                    self._cap_status = "检测到多个电量字节，请换一个相差至少 10% 的电量再抓一次"
                self.render()
                return

            if second:
                self._cap_a = None
                self._ambiguous = False
                self._cap_status = "两次抓包没有找到随电量变化的字节，请重新抓取"
            else:
                self._ambiguous = False
                self._cap_status = "没有匹配的电量数据，可能是操作不规范，请重复操作一遍"
            self.render()
            return

        self._ambiguous = False
        parts = [f"已停止：{os.path.basename(self._cap_file)}{detail}"]
        if devs:
            parts.append("、".join(f"{v:04X}:{p:04X}" for v, p in devs[:2]))
        if frames:
            parts.append(f"帧 {len(frames)}")
        elif not devs:
            parts.append("未识别到设备")
        self._cap_status = " · ".join(parts)
        self.render()

    def _purge_capture_files(self):
        outdir = os.path.join(tempfile.gettempdir(), "BatteryTrayCapture")
        for name in ("capture.pcap", "status.json", "stop.flag"):
            try:
                os.remove(os.path.join(outdir, name))
            except Exception:
                pass

    def close(self, animate=False):
        if self._closing:
            return
        self._closing = True
        try:
            if self._capturing:
                self._capturing = False
                if self._cap_stop:
                    with open(self._cap_stop, "w", encoding="utf-8") as f:
                        f.write("1")
            if self._cap_proc:
                try:
                    kernel32.TerminateProcess(self._cap_proc, 0)
                    kernel32.CloseHandle(self._cap_proc)
                except Exception:
                    pass
                self._cap_proc = None
            self._purge_capture_files()
        except Exception:
            pass
        self.parent._help_win = None

        def finish():
            try:
                if self.shadow:
                    self.shadow.destroy()
            except Exception:
                pass
            self.shadow = None
            try:
                self.root.destroy()
            except Exception:
                pass
        if animate:
            self._close_anim(finish)
        else:
            finish()


class AddDeviceWindow(_Fluent):
    """发现新设备时的确认对话框（Fluent ContentDialog）。"""

    LW, LH = 400, 252

    def __init__(self, app, vid: int, pid: int, name: str, level=None):
        self.app = app
        self.vid = vid
        self.pid = pid
        self.name = name
        self.level = level
        self.parent = app.panel
        self._closed = False
        self._new_window(self.parent.root, "发现新设备", app.theme_light,
                         self.LW, self.LH)
        self.cv.bind("<Button-1>", lambda e: self._drag_press(
            e, 52, ("btn_",)))
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>",
                     lambda e: setattr(self, "_drag", None))
        self.root.bind("<Return>", lambda e: self._done(True))
        self.root.bind("<Escape>", lambda e: self._done(False))
        x, y = self._center_on_work_area()
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                           self.c["stroke"])
        self.shadow = None
        if not IS_WIN11:
            try:
                self.shadow = ShadowWindow(self)
            except Exception:
                self.shadow = None
        self.render()
        self._open_anim(x, y)
        try:
            self.root.focus_force()
        except Exception:
            pass

    def render(self):
        c = self.c
        cv = self.cv
        cv.delete("all")
        self._btns = {}
        W, H = self.LW, self.LH
        fy = H - 72
        cv.create_image(0, 0, anchor="nw", image=self._grad(W, 0, fy, fy))
        self._ctext(24, 34, "发现新设备", c["text"], self._f(15, True),
                    anchor="w")
        cv.create_image(14, 56, anchor="nw",
                        image=self._img(self._spec_shadow(W - 48, 72, 6)))
        cv.create_image(24, 64, anchor="nw",
                        image=self._rr(W - 48, 72, 6, c["card"],
                                       c["stroke_top"], c["stroke_bot"]))
        col = level_rgb(self.level, c)
        well = (_mix(c["card"], col, 0.18) if self.level is not None
                else c["well"])
        cv.create_image(38, 81, anchor="nw", image=self._disc(38, well))
        self._ctext(57, 100, _device_glyph(self.name),
                    _mix(col, c["text"], 0.35) if self.level is not None
                    else c["text"], self._fi(16))
        self._ctext(90, 90, self._fit(self.name or "未知设备",
                                      self._f(12, True), W - 48 - 66 - 70),
                    c["text"], self._f(12, True), anchor="w")
        self._ctext(90, 112, f"{self.vid:04X}:{self.pid:04X}", c["sub"],
                    self._f(10), anchor="w")
        rx = W - 24 - 14 - 44
        ring = cv.create_image(rx, 78, anchor="nw",
                               image=self._ring(44, 4.5, 0 if self.level
                                                is not None else None, col))
        num = self._ctext(rx + 22, 100, "–" if self.level is None else "0",
                          c["text"] if self.level is not None else c["dim"],
                          self._fn(11))
        self._ctext(24, 160, "添加后会显示在托盘和电量面板里，双击设备名可以改名。",
                    c["sub"], self._f(10), anchor="w")
        cv.create_rectangle(0, fy, W, H, fill=_rgb(c["footer"]), outline="")
        cv.create_line(0, fy, W, fy, fill=_rgb(c["divider"]),
                       width=self._px(1))
        bw = (W - 48 - 8) / 2
        self._button(24, fy + 20, bw, 32, "添加", lambda: self._done(True),
                     kind="accent", tag="btn_add")
        self._button(24 + bw + 8, fy + 20, bw, 32, "暂不",
                     lambda: self._done(False), tag="btn_cancel")
        cv.scale("all", 0, 0, self.S, self.S)
        if self.level is not None:
            target = int(self.level)

            def step(e):
                v = int(round(target * e))
                cv.itemconfigure(ring, image=self._ring(44, 4.5, v, col))
                cv.itemconfigure(num, text=str(v))
            self._anim(0.3 + 0.6 * target / 100, step, delay=0.18,
                       key="ring")

    def _done(self, ok: bool):
        if self._closed:
            return
        self._closed = True
        if ok:
            try:
                self.app.add_device(self.vid, self.pid, self.name)
            except Exception:
                pass
        self.app._add_win = None

        def finish():
            try:
                if self.shadow:
                    self.shadow.destroy()
            except Exception:
                pass
            try:
                self.root.destroy()
            except Exception:
                pass
        self._close_anim(finish)


class FluentMenu(_Fluent):
    """卡片右键菜单。items: [(glyph, 文字, 回调) | None(分隔线)]。"""

    LW = 200
    ROW = 32

    def __init__(self, panel, items, x_root, y_root):
        self.panel = panel
        self.items = items
        self.LH = 8 + sum(self.ROW if it else 9 for it in items)
        self._new_window(panel.root, "", panel.light, self.LW, self.LH,
                         bg_key="menu")
        _l, _t, r, b, _sw, _sh = work_area()
        x = min(int(x_root), r - self.W - 4)
        up = y_root + self.H > b
        y = int(y_root) - self.H if up else int(y_root)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                           self.c["stroke"])
        self.render()
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("<FocusOut>", lambda e: self.root.after(
            80, self._check_focus))
        self._open_anim(x, y, dy=6 if up else -6, dur=0.16)
        try:
            self.root.focus_force()
        except Exception:
            pass

    def render(self):
        c = self.c
        cv = self.cv
        W = self.LW
        cv.create_rectangle(0, 0, W, self.LH, fill=_rgb(c["menu"]),
                            outline="")
        if not IS_WIN11:
            cv.create_image(0, 0, anchor="nw", image=self._rr(
                W, self.LH, 8, None, c["ctl_stroke"]))
        y = 4
        for i, it in enumerate(self.items):
            if it is None:
                cv.create_line(4, y + 4, W - 4, y + 4,
                               fill=_rgb(c["divider"]), width=self._px(1))
                y += 9
                continue
            glyph, label, cmd = it
            tag = f"mi_{i}"
            cv.create_rectangle(4, y, W - 4, y + self.ROW,
                                fill=_rgb(c["menu"]), outline="", tags=(tag,))
            hv = cv.create_image(4, y + 2, anchor="nw", tags=(tag,),
                                 state="hidden",
                                 image=self._rr(W - 8, self.ROW - 4, 4,
                                                c["menu_hover"]))
            self._ctext(24, y + self.ROW / 2, glyph, c["text"], self._fi(12),
                        tags=(tag,))
            self._ctext(44, y + self.ROW / 2, label, c["text"], self._f(10),
                        tags=(tag,), anchor="w")
            cv.tag_bind(tag, "<Enter>", lambda e, h=hv: cv.itemconfigure(
                h, state="normal"))
            cv.tag_bind(tag, "<Leave>", lambda e, h=hv: cv.itemconfigure(
                h, state="hidden"))
            cv.tag_bind(tag, "<ButtonRelease-1>",
                        lambda e, f=cmd: self._pick(f))
            y += self.ROW
        cv.scale("all", 0, 0, self.S, self.S)

    def _pick(self, cmd):
        self.close()
        try:
            cmd()
        except Exception:
            pass

    def _check_focus(self):
        try:
            f = self.root.focus_get()
        except Exception:
            f = None
        try:
            if f is not None and f.winfo_toplevel() is self.root:
                return
        except Exception:
            pass
        self.close(refocus=False)
        self.panel._check_dismiss()

    def close(self, refocus: bool = True):
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.panel, "_menu", None) is self:
            self.panel._menu = None
        # 焦点还给面板，之后点外面才能照常收起
        if refocus and self.panel.visible:
            try:
                self.panel.root.focus_force()
            except Exception:
                pass


class Panel(_Fluent):
    """托盘弹出面板：仿 Windows 11 快速设置 / 电池浮出窗口。"""

    LW = 360
    HEAD_H = 56
    FOOT_H = 52
    CARD_H = 68
    GAP = 6
    MAX_ROWS = 5
    EMPTY_H = 156
    RING = 44

    def __init__(self, app: "TrayApp"):
        self.app = app
        self.root = tk.Tk()
        self.root.title("2.4G")
        self.root.overrideredirect(True)
        self._fl_init(app.theme_light)
        self.W = self._px(self.LW)
        self.LH = self.HEAD_H + self.EMPTY_H + 12 + self.FOOT_H
        self.H = self._px(self.LH)
        self.root.configure(bg=_rgb(self.c["bg"]))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0,
                            bg=_rgb(self.c["bg"]))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-3>", self._on_right_click)
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Escape>", lambda e: self.hide())
        self.root.bind("<FocusOut>", self._on_focus_out, add="+")
        self.root.bind_all("<Button-1>", self._maybe_commit, add="+")
        if app.panel_only:
            self.cv.bind("<Button-1>", lambda e: self._drag_press(
                e, self.HEAD_H, ("btn_",)))
            self.cv.bind("<B1-Motion>", self._drag_move)
            self.cv.bind("<ButtonRelease-1>",
                         lambda e: setattr(self, "_drag", None))
        self._entry = None
        self._entry_key = None
        self._entry_win = None
        self._entry_item = None
        self._entry_guard = 0.0
        self._name_font = None
        self._name_limit_px = 0
        self._name_max_chars = 0
        self._help_win = None
        self._menu = None
        self._modal = False
        self._scroll = 0.0
        self._render_scroll = 0.0
        self._max_scroll = 0.0
        self._list = (self.HEAD_H, self.HEAD_H)
        self._thumb = None
        self._card_rects = {}
        self._card_bg = {}
        self._hover_key = None
        self._hover_t = {}
        self._shown_lv = {}
        self._badges = []
        self._bob = None
        self._spin_angle = 0.0
        self._spin_until = 0.0
        self._ambient_on = False
        self._visible = False
        self._closing = False
        self._shown_at = 0.0
        self._dismissed_at = 0.0
        self._edge = "bottom"
        self._startup_var = startup_enabled()
        self.shadow = None
        # 先映射一次（全透明）拿到真正的顶层窗口，设好圆角/阴影再藏起来
        try:
            self.root.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.root.geometry(f"{self.W}x{self.H}+0+0")
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                           self.c["stroke"])
        if not IS_WIN11:
            try:
                self.shadow = ShadowWindow(self)
            except Exception:
                self.shadow = None
        self.root.withdraw()
        self.root.after(200, self._prewarm)

    @property
    def visible(self) -> bool:
        return self._visible

    def _prewarm(self):
        """后台线程提前生成卡片、圆环、开关的位图，动画第一次就流畅。"""
        c = self.c
        w = self.LW - 24
        specs = [self._spec_card(k) for k in range(5)]
        specs.append(self._spec_shadow(w, self.CARD_H, 6))
        specs += [self._spec_ring(self.RING, 4.5, lv, level_rgb(lv, c))
                  for lv in range(0, 101)]
        specs += [self._spec_toggle(f, hv) for f in range(7)
                  for hv in (False, True)]
        todo = [s for s in specs if s[0] not in _B64]

        def work():
            for key, make in todo:
                try:
                    _B64.setdefault(key, make())
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _spec_card(self, k):
        c = self.c
        return self._spec_rr(self.LW - 24, self.CARD_H, 6,
                             _mix(c["card"], c["card_hover"], k / 4.0),
                             c["stroke_top"], c["stroke_bot"])

    # -- 显示 / 隐藏 ---------------------------------------------------------
    def _anchor(self, h=None):
        h = self.H if h is None else h
        l, t, r, b, sw, sh = work_area()
        m = self._px(12)
        if self.app.panel_only:
            self._edge = "center"
            return (l + max(0, (r - l - self.W) // 2),
                    t + max(0, (b - t - h) // 2))
        if t > 0:
            self._edge = "top"
            return r - self.W - m, t + m
        if l > 0:
            self._edge = "left"
            return l + m, b - h - m
        self._edge = "right" if r < sw else "bottom"
        return r - self.W - m, b - h - m

    def _edge_offset(self, d):
        return {"bottom": (0, d), "top": (0, -d), "right": (d, 0),
                "left": (-d, 0)}.get(self._edge, (0, d // 2))

    def show(self):
        if self._visible:
            try:
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass
            return
        self._cancel("win")
        self._closing = False
        self._startup_var = startup_enabled()
        self._set_theme(self.app.theme_light)
        self._scroll = 0.0
        self._shown_lv = {}
        self._hover_key = None
        self.render(entrance=True)
        self._visible = True
        x, y = self._anchor()
        try:
            self.root.attributes("-alpha", 0.0)
        except Exception:
            pass
        dx, dy = self._edge_offset(self._px(20))
        self.root.geometry(f"{self.W}x{self.H}+{x + dx}+{y + dy}")
        self.root.deiconify()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.update_idletasks()
        apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                           self.c["stroke"])
        self._shown_at = time.time()

        def step(e):
            ox, oy = self._edge_offset(int(round(self._px(20) * (1 - e))))
            self.root.geometry(f"+{x + ox}+{y + oy}")
            self.root.attributes("-alpha", min(1.0, e * 1.4))

        def done():
            if self.shadow and self._visible:
                self.shadow.show(x, y)
        self._anim(0.3, step, key="win", done=done)
        try:
            self.root.focus_force()
        except Exception:
            pass
        self._start_ambient()

    def _on_focus_out(self, e):
        if self.app.panel_only or not self._visible:
            return
        self.root.after(100, self._check_dismiss)

    def _check_dismiss(self):
        """点到面板以外的地方就收起（Windows 浮出窗口的轻触关闭）。"""
        if (not self._visible or self.app.panel_only or self._entry
                or self._menu or self._modal):
            return
        if time.time() - self._shown_at < 0.3:
            return
        try:
            f = self.root.focus_get()
        except Exception:
            f = None
        if f is None:
            self.hide(dismissed=True)

    def hide(self, dismissed: bool = False):
        if self._entry:
            self._commit_name(True)
        self._close_menu()
        if self.app.panel_only:
            self.app.quit()
            return
        if not self._visible:
            return
        self._visible = False
        self._closing = True
        if dismissed:
            self._dismissed_at = time.time()
        if self.shadow:
            try:
                self.shadow.hide()
            except Exception:
                pass
        x, y = self.root.winfo_x(), self.root.winfo_y()

        def step(e):
            ox, oy = self._edge_offset(int(round(self._px(12) * e)))
            self.root.geometry(f"+{x + ox}+{y + oy}")
            self.root.attributes("-alpha", 1 - e)
        self._anim(0.14, step, key="win", done=self._withdraw, ease=ease_in)

    def _withdraw(self):
        self._cancel("win")
        self._visible = False
        self._closing = False
        if self.shadow:
            try:
                self.shadow.hide()
            except Exception:
                pass
        try:
            self.root.attributes("-topmost", False)
            self.root.withdraw()
        except Exception:
            pass

    def _close_help(self):
        hw = self._help_win
        if hw:
            try:
                hw.close()
            except Exception:
                pass
        self._help_win = None

    def _close_menu(self):
        if self._menu:
            try:
                self._menu.close(refocus=False)
            except Exception:
                pass
            self._menu = None

    def close(self):
        self._close_help()
        self._close_menu()
        if self.shadow:
            try:
                self.shadow.destroy()
            except Exception:
                pass
            self.shadow = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def _resize(self, lh):
        """设备数变化时改高度：可见时平滑伸缩，保持贴住任务栏的那条边。"""
        self.LH = lh
        H = self._px(lh)
        if H == self.H:
            return
        old = self.H
        self.H = H
        self.cv.configure(width=self.W, height=self.H)
        if not self._visible:
            return
        if self.shadow:
            try:
                self.shadow.destroy()
            except Exception:
                pass
            self.shadow = None

        def step(e):
            h = int(round(old + (H - old) * e))
            x, y = self._anchor(h)
            self.root.geometry(f"{self.W}x{h}+{x}+{y}")

        def done():
            apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                               self.c["stroke"])
            if not IS_WIN11:
                try:
                    self.shadow = ShadowWindow(self)
                    self.shadow.show(*self._anchor())
                except Exception:
                    self.shadow = None
        self._anim(0.2, step, key="win_size", done=done, ease=ease_in_out)

    # -- 绘制 ----------------------------------------------------------------
    def render(self, entrance: bool = False):
        if self._entry:
            return
        c = self.c
        cv = self.cv
        snap = self.app.snapshot()
        keys = list(snap.keys())
        n = len(keys)
        if n:
            vis = min(n, self.MAX_ROWS)
            list_h = vis * self.CARD_H + (vis - 1) * self.GAP
            content = n * self.CARD_H + (n - 1) * self.GAP
        else:
            list_h = content = self.EMPTY_H
        self._max_scroll = max(0.0, float(content - list_h))
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))
        top = self.HEAD_H
        self._list = (top, top + list_h)
        self._resize(top + list_h + 12 + self.FOOT_H)

        for k in ("card", "hover", "scroll", "tog", "hv_", "bob"):
            self._cancel(k)
        cv.delete("all")
        self._btns = {}
        self._card_rects = {}
        self._card_bg = {}
        self._badges = []
        self._bob = None
        self._thumb = None
        self._card_texts = {}
        self._scroll_target = self._scroll
        W, LH = self.LW, self.LH
        foot_y = top + list_h + 12
        cv.create_image(0, 0, anchor="nw", image=self._grad(W, 0, foot_y,
                                                            foot_y))
        self._render_scroll = self._scroll
        if n:
            y0 = top - self._scroll
            step = self.CARD_H + self.GAP
            for i, key in enumerate(keys):  # 先铺全部投影，避免压到上一张卡
                y = y0 + i * step
                cv.create_image(12 - 10, y - 10 + 2, anchor="nw",
                                tags=("lst", f"card_{key}"),
                                image=self._img(self._spec_shadow(
                                    W - 24, self.CARD_H, 6)))
            for i, key in enumerate(keys):
                y = y0 + i * step
                self._card(i, 12, y, W - 12, y + self.CARD_H, key, snap[key],
                           entrance)
        else:
            self._empty(12, top, W - 12, top + list_h)

        # 可滚动时，用同一段渐变遮住滚出视口的卡片
        if self._max_scroll > 0:
            cv.create_image(0, 0, anchor="nw",
                            image=self._grad(W, 0, top, foot_y))
            cv.create_image(0, top + list_h, anchor="nw",
                            image=self._grad(W, top + list_h, 12, foot_y))

        # 标题栏
        self._ctext(18, 29, "外设电量", c["text"], self._f(13, True),
                    anchor="w")
        tw = tkfont.Font(root=self.root, font=self._f(13, True)).measure(
            "外设电量") / self.S
        hx = 18 + tw + 8
        if n:
            item = self._ctext(hx, 30, f"{n} 台设备", c["dim"], self._f(10),
                               anchor="w")
            try:
                hx = cv.bbox(item)[2] + 8
            except Exception:
                hx += 60
        low = sum(1 for r in snap.values()
                  if r.ok and r.level is not None and r.level < 20
                  and not r.stale)
        if low:
            text = f"{low} 台电量低"
            cw = tkfont.Font(root=self.root, font=self._f(9, True)).measure(
                text) / self.S + 16
            cv.create_image(hx, 20, anchor="nw",
                            image=self._rr(cw, 20, 10,
                                           _mix(c["bg_top"], c["bad"], 0.18)))
            self._ctext(hx + cw / 2, 30, text, c["bad"], self._f(9, True))
        bx = W - 12 - 32
        if self.app.panel_only:
            self._button(bx, 13, 32, 32, GLYPH["close"], self.hide,
                         kind="subtle", tag="btn_close", glyph=True, size=11)
            bx -= 36
        self._button(bx, 13, 32, 32, GLYPH["refresh"], self._refresh,
                     kind="subtle", tag="btn_refresh", glyph=True, size=13)

        # 底栏
        cv.create_rectangle(0, foot_y, W, LH, fill=_rgb(c["footer"]),
                            outline="")
        cv.create_line(0, foot_y, W, foot_y, fill=_rgb(c["divider"]),
                       width=self._px(1))
        self._ctext(18, foot_y + self.FOOT_H / 2, self._updated_text(),
                    c["sub"], self._f(10), anchor="w")
        tx = W - 18 - 40
        self._toggle(tx, foot_y + (self.FOOT_H - 20) / 2, self._startup_var)
        self._ctext(tx - 10, foot_y + self.FOOT_H / 2, "开机自启", c["text"],
                    self._f(10), anchor="e", tags=("tog",))
        if not IS_WIN11:
            cv.create_image(0, 0, anchor="nw", image=self._rr(
                W, LH, 8, None, c["ctl_stroke"]))
        self._scrollbar()
        cv.scale("all", 0, 0, self.S, self.S)
        if entrance:
            self._entrance(keys)

    def _entrance(self, keys):
        """卡片依次上浮淡入（文字颜色从卡片底色过渡）。"""
        S, c = self.S, self.c
        for i, key in enumerate(keys):
            tag = f"card_{key}"
            texts = self._card_texts.get(key, [])
            self.cv.move(tag, 0, 14 * S)
            for item, _col in texts:
                self.cv.itemconfigure(item, fill=_rgb(c["card"]))

            def step(e, tag=tag, texts=texts, moved=[0.0]):
                off = 14 * S * e
                self.cv.move(tag, 0, -(off - moved[0]))
                moved[0] = off
                for item, col in texts:
                    self.cv.itemconfigure(item, fill=_rgb(
                        _mix(c["card"], col, min(1.0, e * 1.3))))
            self._anim(0.36, step, delay=0.05 + i * 0.05, key=f"card_in_{key}")

    def refresh_view(self):
        """数据更新后重绘；入场/开关/滚动动画进行中就稍后再画，不打断动效。"""
        busy = any(a[4] and a[4].startswith(("card_in", "win", "tog",
                                               "scroll"))
                   for a in self._anims if not a[6])
        if busy:
            self.root.after(120, self.refresh_view)
        elif self._visible:
            self.render()

    def _updated_text(self) -> str:
        if self.app._refreshing:
            return "正在刷新…"
        rs = self.app.snapshot()
        times = [r.ts for r in rs.values() if r.ts and not r.stale]
        if not times:
            return "还没有设备" if not rs else "暂无新读数"
        return "上次更新 " + time.strftime("%H:%M:%S",
                                          time.localtime(max(times)))

    def _subtitle(self, r: Reading):
        """返回 (文字, 是否附带“设置读取”链接)。"""
        if r.status == "unknown_proto":
            return "协议未知", True
        if r.stale:
            return r.detail or "上次读数", False
        if r.status == "asleep":
            return r.detail or "设备休眠", False
        if not r.present or r.status == "unplugged":
            return "未连接", False
        if r.ok:
            st = STATUS_TEXT.get(r.status, "")
            if r.status not in ("charging", "full", "wired"):
                st = "已连接"
            if r.level is not None and r.level < 20:
                st = "电量低"
            meta = f"{r.vid:04X}:{r.pid:04X}" if r.vid else ""
            return (f"{st} · {meta}" if meta else st), False
        return r.detail or "未连接", False

    def _card(self, i, x1, y1, x2, y2, key, r: Reading, entrance):
        c = self.c
        cv = self.cv
        tag = f"card_{key}"
        tags = ("lst", tag)
        lv = r.level if (r.ok and r.level is not None) else None
        col = level_rgb(lv, c)
        if r.stale and lv is not None:
            col = _mix(col, c["track"], 0.45)
        k = 4 if key == self._hover_key else 0
        self._hover_t[key] = k / 4.0
        bg = cv.create_image(x1, y1, anchor="nw", tags=tags,
                             image=self._img(self._spec_card(k)))
        self._card_rects[key] = (x1, y1, x2, y2)
        self._card_bg[key] = bg
        texts = []
        cy = (y1 + y2) / 2

        # 设备图标：底色带一点电量色
        well = _mix(c["card"], col, 0.16) if lv is not None else c["well"]
        cv.create_image(x1 + 14, cy - 19, anchor="nw", tags=tags,
                        image=self._disc(38, well))
        gcol = _mix(col, c["text"], 0.35) if lv is not None else c["text"]
        texts.append((self._ctext(x1 + 33, cy, _device_glyph(r.name), gcol,
                                  self._fi(15), tags=tags), gcol))
        if r.ok and r.status == "charging" and not r.stale:
            b = cv.create_image(x1 + 38, cy + 5, anchor="nw", tags=tags,
                                image=self._disc(16, c["accent"]))
            self._badges.append(b)
            self._ctext(x1 + 46, cy + 13, GLYPH["bolt"], c["on_accent"],
                        self._fi(8), tags=tags)

        tx = x1 + 64
        rx = x2 - 14 - self.RING
        name_font = self._f(11, True)
        nm = self._ctext(tx, cy - 10, self._fit(r.name or "设备", name_font,
                                               rx - 12 - tx),
                         c["text"], name_font, anchor="w",
                         tags=tags + (f"nm_{key}",))
        texts.append((nm, c["text"]))
        cv.tag_bind(f"nm_{key}", "<Double-Button-1>",
                    lambda e, k=key: self._edit_name_at(k))
        sub, need_fix = self._subtitle(r)
        low = lv is not None and lv < 20 and not r.stale
        sub_font = self._f(9)
        sub = self._fit(sub, sub_font, rx - 12 - tx - (64 if need_fix else 0))
        scol = c["bad"] if low else c["sub"]
        item = self._ctext(tx, cy + 12, sub, scol, sub_font, anchor="w",
                           tags=tags)
        texts.append((item, scol))
        if need_fix:
            try:
                sx = cv.bbox(item)[2] + 8
            except Exception:
                sx = tx + 60
            link = self._link(sx, cy + 12, "设置读取",
                              lambda k=key: self.open_help(k),
                              f"fix_{key}", size=9)
            cv.addtag_withtag("lst", link)
            cv.addtag_withtag(tag, link)
            texts.append((link, c["link"]))

        # 电量圆环：从上次显示值（新打开时从 0）扫到当前值，数字同步计数
        ring_y = cy - self.RING / 2
        target = lv
        start = 0 if (entrance or key not in self._shown_lv) else \
            self._shown_lv[key]
        if target is None:
            ring = cv.create_image(rx, ring_y, anchor="nw", tags=tags,
                                   image=self._ring(self.RING, 4.5, None,
                                                    col))
            num = self._ctext(rx + self.RING / 2, cy, "–", c["dim"],
                              self._fn(12), tags=tags)
            texts.append((num, c["dim"]))
            self._shown_lv.pop(key, None)
        else:
            start = target if start is None else start
            ncol = c["sub"] if r.stale else c["text"]
            ring = cv.create_image(rx, ring_y, anchor="nw", tags=tags,
                                   image=self._ring(self.RING, 4.5, start,
                                                    col))
            num = self._ctext(rx + self.RING / 2, cy, str(start), ncol,
                              self._fn(11 if target >= 100 else 12),
                              tags=tags)
            texts.append((num, ncol))
            self._shown_lv[key] = start
            if start != target:
                def step(e, s=start, t=target, ring=ring, num=num, col=col,
                         key=key):
                    v = int(round(s + (t - s) * e))
                    cv.itemconfigure(ring, image=self._ring(self.RING, 4.5,
                                                            v, col))
                    cv.itemconfigure(num, text=str(v))
                    self._shown_lv[key] = v
                dur = 0.35 + 0.6 * abs(target - start) / 100.0
                self._anim(dur, step, delay=(0.12 + i * 0.05) if entrance
                           else 0.0, key=f"card_ring_{key}")
        self._card_texts[key] = texts
        cv.tag_bind(tag, "<Enter>", lambda e, k=key: self._hover_card(k, True))
        cv.tag_bind(tag, "<Leave>", lambda e, k=key: self._hover_card(k, False))

    def _hover_card(self, key, on):
        """悬停底色 5 帧过渡（约 120ms）。"""
        if on:
            self._hover_key = key
        elif self._hover_key == key:
            self._hover_key = None
        item = self._card_bg.get(key)
        if not item:
            return
        t0 = self._hover_t.get(key, 0.0)
        t1 = 1.0 if on else 0.0

        def step(e):
            t = t0 + (t1 - t0) * e
            self._hover_t[key] = t
            self.cv.itemconfigure(item, image=self._img(
                self._spec_card(int(round(t * 4)))))
        self._anim(0.12, step, key=f"hover_{key}", ease=lambda t: t)

    def _empty(self, x1, y1, x2, y2):
        c = self.c
        cv = self.cv
        cv.create_image(x1 - 10, y1 - 8, anchor="nw",
                        image=self._img(self._spec_shadow(x2 - x1, y2 - y1,
                                                          6)))
        cv.create_image(x1, y1, anchor="nw",
                        image=self._rr(x2 - x1, y2 - y1, 6, c["card"],
                                       c["stroke_top"], c["stroke_bot"]))
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        cv.create_image(cx - 28, cy - 58, anchor="nw",
                        image=self._disc(56, _mix(c["card"], c["accent"],
                                                  0.14)))
        icon = self._ctext(cx, cy - 30, GLYPH["device"], c["link"],
                           self._fi(24))
        self._bob = (icon, cy - 30)
        self._ctext(cx, cy + 16, "没有设备", c["text"], self._f(12, True))
        self._ctext(cx, cy + 40, "插入或重新拔插 2.4G 接收器即可添加",
                    c["sub"], self._f(10))

    def _toggle(self, x, y, on):
        cv = self.cv
        frame = 6 if on else 0
        item = cv.create_image(x, y, anchor="nw", tags=("tog",),
                               image=self._img(self._spec_toggle(frame,
                                                                 False)))
        self._tog = [item, frame, False]
        cv.tag_bind("tog", "<Button-1>", lambda e: self._toggle_startup())
        cv.tag_bind("tog", "<Enter>", lambda e: self._tog_hover(True))
        cv.tag_bind("tog", "<Leave>", lambda e: self._tog_hover(False))

    def _tog_hover(self, on):
        item, frame, _ = self._tog
        self._tog[2] = on
        try:
            self.cv.itemconfigure(item, image=self._img(
                self._spec_toggle(frame, on)))
        except tk.TclError:
            pass

    def _scrollbar(self):
        if self._max_scroll <= 0:
            return
        top, bot = self._list
        track = bot - top
        content = self._max_scroll + track
        th = max(24.0, track * track / content)
        ty = top + 4 + (self._scroll / self._max_scroll) * (track - 8 - th)
        item = self.cv.create_image(self.LW - 7, ty, anchor="nw",
                                    image=self._rr(3, th, 1.5,
                                                   self.c["dim"]))
        self._thumb = (item, top + 4, track - 8 - th)

    # -- 常驻小动效：刷新图标旋转、空状态图标浮动、充电徽标呼吸 -------------
    def _start_ambient(self):
        if not self._ambient_on:
            self._ambient_on = True
            self.root.after(30, self._ambient)

    def _ambient(self):
        if not self._visible:
            self._ambient_on = False
            return
        cv = self.cv
        now = time.perf_counter()
        info = self._btns.get("btn_refresh")
        spinning = self.app._refreshing or now < self._spin_until
        if spinning:
            self._spin_angle = (self._spin_angle + 10) % 360
        elif self._spin_angle:
            self._spin_angle = self._spin_angle + 10
            if self._spin_angle >= 360:
                self._spin_angle = 0.0
        if info:
            try:
                cv.itemconfigure(info[2], angle=-self._spin_angle)
            except tk.TclError:
                pass
        if self._bob:
            item, base = self._bob
            try:
                x, _y = cv.coords(item)
                cv.coords(item, x, (base + 3 * math.sin(now * 2.2)) * self.S)
            except Exception:
                pass
        if self._badges:
            k = (math.sin(now * 3.2) + 1) / 2
            img = self._disc(16, _mix(self.c["accent"], (255, 255, 255),
                                      0.35 * round(k * 4) / 4))
            for b in self._badges:
                try:
                    cv.itemconfigure(b, image=img)
                except tk.TclError:
                    pass
        self.root.after(33, self._ambient)

    # -- 交互 ----------------------------------------------------------------
    def _refresh(self):
        self._spin_until = time.perf_counter() + 0.7
        self.app.refresh()
        self.render()

    def _on_wheel(self, e):
        if self._entry:
            self._commit_name(True)
        if self._max_scroll <= 0:
            return
        unit = self.CARD_H + self.GAP
        target = getattr(self, "_scroll_target", self._scroll)
        target = max(0.0, min(self._max_scroll,
                              target + (-unit if e.delta > 0 else unit)))
        self._scroll_target = target
        s0 = self._scroll

        def step(ev):
            new = s0 + (target - s0) * ev
            self.cv.move("lst", 0, -(new - self._scroll) * self.S)
            self._scroll = new
            if self._thumb:
                item, y0, travel = self._thumb
                x = self.cv.coords(item)[0]
                self.cv.coords(item, x, (y0 + travel * new / self._max_scroll)
                               * self.S)

        def done():
            self._scroll_target = self._scroll
        self._anim(0.22, step, key="scroll", done=done)

    def _card_at(self, lx, ly):
        top, bot = self._list
        if not top <= ly <= bot:
            return None
        ly += self._scroll - self._render_scroll
        for key, (x1, y1, x2, y2) in self._card_rects.items():
            if x1 <= lx <= x2 and y1 <= ly <= y2:
                return key
        return None

    def _on_right_click(self, e):
        self._close_menu()
        key = self._card_at(e.x / self.S, e.y / self.S)
        if key is None:
            return
        r = self.app.snapshot().get(key)
        items = [(GLYPH["rename"], "重命名",
                  lambda k=key: self._edit_name_at(k))]
        if r is not None and r.status == "unknown_proto":
            items.append((GLYPH["settings"], "设置读取…",
                          lambda k=key: self.open_help(k)))
        items.append((GLYPH["refresh"], "立即刷新", self._refresh))
        items.append(None)
        items.append((GLYPH["delete"], "删除设备",
                      lambda k=key: self.delete_device(k)))
        self._menu = FluentMenu(self, items, e.x_root, e.y_root)

    def delete_device(self, key: str):
        self._close_menu()
        try:
            self.app.remove_device(key)
        except Exception:
            pass
        self.render()

    def open_help(self, key=None):
        old = self._help_win
        if old is not None:
            if getattr(old, "key", None) == key:
                try:
                    old.root.lift()
                    old.root.focus_force()
                except Exception:
                    pass
                return
            self._close_help()
        self._help_win = HelpWindow(self, key)
        if not self.app.panel_only:
            self._withdraw()

    def _toggle_startup(self):
        """开关先滑过去，动画结束再真正改开机自启（失败则弹回）。"""
        want = not self._startup_var
        if not self._visible or not hasattr(self, "_tog"):
            self._apply_startup(want)
            return
        item, frame, hover = self._tog
        f0, f1 = frame, 6 if want else 0

        def step(e):
            f = int(round(f0 + (f1 - f0) * e))
            self._tog[1] = f
            self.cv.itemconfigure(item, image=self._img(
                self._spec_toggle(f, self._tog[2])))

        def done():
            self._apply_startup(want)
            self.render()
        self._anim(0.2, step, key="tog", done=done, ease=ease_in_out)

    def _apply_startup(self, want: bool):
        try:
            if want:
                self._startup_var = install_startup()
            else:
                remove_startup()
                self._startup_var = False
        except Exception as e:
            self._modal = True
            try:
                messagebox.showerror("开机自启", f"设置失败：{e}",
                                     parent=self.root)
            finally:
                self._modal = False

    # -- 改名 ----------------------------------------------------------------
    def _edit_name_at(self, key):
        ids = self.cv.find_withtag(f"nm_{key}")
        if not ids:
            return
        x1, y1, _x2, y2 = self.cv.bbox(ids[0])
        self._edit_name(key, x1, (y1 + y2) / 2)

    def _edit_name(self, key, x, y):
        if self._entry:
            self._commit_name(True)
        self._entry_guard = time.time()
        r = self.app.snapshot().get(key)
        current = (r.name if r and r.name else "")
        S = self.S
        c = self.c
        limit_px = int((self.LW - 64 - 12 - 14 - self.RING - 24) * S)
        font = tkfont.Font(root=self.root, family=FONT,
                           size=-int(round(11 * S)), weight="bold")
        cjk = max(1, font.measure("汉"))
        eng = max(1.0, font.measure("abcdefghij") / 10.0)
        avg = cjk * 0.4 + eng * 0.6
        self._name_font = font
        self._name_limit_px = limit_px
        self._name_max_chars = max(4, int(limit_px / max(1.0, avg)))
        e = tk.Entry(self.root, bg=_rgb(c["ctl"]), fg=_rgb(c["text"]),
                     insertbackground=_rgb(c["text"]), relief="flat", bd=0,
                     highlightthickness=max(1, int(S)),
                     highlightbackground=_rgb(c["ctl_stroke"]),
                     highlightcolor=_rgb(c["accent"]),
                     font=(FONT, -int(round(11 * S)), "bold"),
                     justify="left", selectbackground=_rgb(c["accent"]),
                     selectforeground=_rgb(c["on_accent"]))
        e.insert(0, current)
        e.configure(validate="key",
                    validatecommand=(self.root.register(self._validate_name),
                                     "%P"))
        self._entry = e
        self._entry_key = key
        self._entry_win = self.cv.create_window(
            x - 4 * S, y, anchor="w", window=e, width=limit_px + 8 * S,
            height=max(24, int(26 * S)))
        self._entry_item = None
        ids = self.cv.find_withtag(f"nm_{key}")
        if ids:
            self._entry_item = ids[0]
            self.cv.itemconfigure(self._entry_item, state="hidden")
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")
        e.bind("<Return>", lambda ev: self._commit_name(True))
        e.bind("<Escape>", self._cancel_name)
        e.bind("<FocusOut>", lambda ev: self._commit_name(True))

    def _cancel_name(self, ev=None):
        self._commit_name(False)
        return "break"

    def _maybe_commit(self, e):
        if self._entry is not None and e.widget is not self._entry:
            if time.time() - self._entry_guard < 0.25:
                return
            self._commit_name(True)

    def _validate_name(self, proposed):
        if len(proposed) > self._name_max_chars:
            return False
        try:
            if self._name_font.measure(proposed) > self._name_limit_px:
                return False
        except Exception:
            pass
        return True

    def _commit_name(self, save):
        e = self._entry
        if not e:
            return
        key = self._entry_key
        text = e.get().strip()
        self._entry = None
        self._entry_key = None
        try:
            e.destroy()
        except Exception:
            pass
        if self._entry_win:
            try:
                self.cv.delete(self._entry_win)
            except Exception:
                pass
            self._entry_win = None
        if self._entry_item:
            try:
                self.cv.itemconfigure(self._entry_item, state="normal")
            except Exception:
                pass
            self._entry_item = None
        if save:
            try:
                self.app.rename_device(key, text)
            except Exception:
                pass
            set_custom_name(key, text)
            if text:
                try:
                    with self.app._lock:
                        rr = self.app._readings.get(key)
                        if rr is not None:
                            rr.name = text
                except Exception:
                    pass
            self.render()
            try:
                self.app.refresh()
            except Exception:
                pass
        else:
            self.render()


# ===========================================================================
# 托盘
# ===========================================================================
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_APP_REFRESH = WM_APP + 2
WM_APP_SHOW = WM_APP + 3
WM_APP_INTERVAL = WM_APP + 4
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETTINGCHANGE = 0x001A
WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
NIF_INFO = 0x10
NIIF_INFO = 0x1
NIIF_USER = 0x4
NIIF_LARGE_ICON = 0x20

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                   ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND,
                                  ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TRAY_OPEN, TRAY_REFRESH, TRAY_STARTUP, TRAY_QUIT = 1, 2, 3, 4


def allow_dark_menus() -> None:
    """让托盘右键菜单跟随系统深色（uxtheme 未公开序号 135/136）。"""
    try:
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]
        set_mode.argtypes = [ctypes.c_int]
        set_mode(1)  # AllowDark
        ux[136]()    # FlushMenuThemes
    except Exception:
        pass


class Tray:
    CLASS_NAME = "BatteryTrayWndDSH"

    def __init__(self, app: "TrayApp"):
        self.app = app
        self.hwnd = 0
        self.icons: dict[int, int] = {}
        self.icon_keys: dict[int, tuple] = {}
        self.thread: Optional[threading.Thread] = None
        self.nids: dict[int, NOTIFYICONDATAW] = {}
        self.enabled = True
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self._proc)
        self._big_icon = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _make_nid(self, uid: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        return nid

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.CLASS_NAME, "2.4G", 0,
                                           0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            return
        allow_dark_menus()
        self._add_icons()
        user32.SetTimer(self.hwnd, 1, max(30, self.app.interval) * 1000, None)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 消息循环结束（WM_CLOSE 毁窗、WM_DESTROY 或 PostQuitMessage）
        # 通知主线程结束 Tk 主循环，绝不留下“窗口没了进程还活着”的僵尸
        self.app._stop = True

    def _add_icons(self):
        self.icons.clear()
        self.nids.clear()
        self.icon_keys.clear()
        self.sync_icons()

    def sync_icons(self):
        """按实际插入的接收器增减托盘图标；一个都没有时清空托盘。"""
        snap = self.app.snapshot()
        present = [k for k, r in snap.items()
                   if r and r.present and r.ok and r.level is not None]
        desired = {i + 1: k for i, k in enumerate(present[:4])}
        for uid in list(self.nids):
            if uid not in desired:
                shell32.Shell_NotifyIconW(NIM_DELETE,
                                          ctypes.byref(self.nids[uid]))
                del self.nids[uid]
                old = self.icons.pop(uid, 0)
                self.icon_keys.pop(uid, None)
                if old:
                    user32.DestroyIcon(old)
        for uid, key in desired.items():
            if uid in self.nids:
                nid = self.nids[uid]
                self._fill_icon(nid, key)
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            else:
                nid = self._make_nid(uid)
                self._fill_icon(nid, key)
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                self.nids[uid] = nid

    def _fill_icon(self, nid: NOTIFYICONDATAW, key: str):
        r = self.app.snapshot().get(key)
        lvl = r.level if (r and r.ok) else None
        charging = bool(r and r.status == "charging")
        k = (lvl is None, lvl, charging, self.app.theme_light)
        old = self.icons.get(nid.uID)
        if self.icon_keys.get(nid.uID) != k or not old:
            icon = make_battery_icon(lvl, self.app.theme_light, charging)
            self.icons[nid.uID] = icon
            self.icon_keys[nid.uID] = k
            nid.hIcon = icon
            if old:
                user32.DestroyIcon(old)
        else:
            nid.hIcon = old
        name = (r.name if r and r.name else "设备")
        stamp = time.strftime("%H:%M", time.localtime(r.ts)) if r else "--:--"
        tip = (f"2.4G · {name}：{r.level}%（{stamp}）" if (r and r.ok)
               else f"2.4G · {name}：未连接（{stamp}）")
        nid.szTip = tip[:127]

    def refresh_icons(self):
        self.sync_icons()

    def notify(self, text: str, title: str = "2.4G") -> bool:
        """托盘气泡通知（Windows 通知中心也会收到），配 64px 满电圆环图标。"""
        nid = self.nids.get(1)
        if nid is None:
            return False
        if not self._big_icon:
            self._big_icon = make_battery_icon(100, self.app.theme_light,
                                               False, size=64)
        old_icon = nid.hIcon
        if self._big_icon:
            nid.hIcon = self._big_icon
        nid.uFlags = NIF_INFO | NIF_ICON
        nid.uVersion = 10000          # 10 秒
        nid.szInfo = text[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        nid.hBalloonIcon = self._big_icon
        ok = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.hIcon = old_icon
        nid.hBalloonIcon = 0
        return bool(ok)

    def destroy(self):
        for uid, nid in list(self.nids.items()):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.nids.clear()
        for h in list(self.icons.values()):
            user32.DestroyIcon(h)
        self.icons.clear()
        if self._big_icon:
            user32.DestroyIcon(self._big_icon)
            self._big_icon = 0

    def _context_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, TRAY_OPEN, "打开电量面板")
            user32.AppendMenuW(menu, MF_STRING, TRAY_REFRESH, "立即刷新")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if
                               startup_enabled() else 0),
                               TRAY_STARTUP, "开机自启")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, TRAY_QUIT, "退出")
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                        pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, 0, 0, 0)  # WM_NULL：让菜单正常收起
        finally:
            user32.DestroyMenu(menu)
        if cmd:
            self.app.tray_command(cmd)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            cmd = lparam & 0xFFFF
            if cmd == WM_LBUTTONUP:
                # 拿到前台权限，面板弹出后才能获得焦点（点外面自动收起）
                user32.SetForegroundWindow(hwnd)
                self.app.request_toggle()
            elif cmd == WM_RBUTTONUP:
                self._context_menu(hwnd)
            return 0
        if msg == WM_TIMER:
            self.app.refresh()
            return 0
        if msg == self.taskbar_created:
            self._add_icons()
            return 0
        if msg == WM_SETTINGCHANGE:
            if lparam:
                try:
                    s = ctypes.wstring_at(lparam)
                    if s == "ImmersiveColorSet":
                        self.app.on_theme_changed()
                except Exception:
                    pass
            return 0
        if msg == WM_DEVICECHANGE:
            if wparam == DBT_DEVNODES_CHANGED:
                self.app._hotplug_requested = True
            return 0
        if msg == WM_APP_REFRESH:
            self.refresh_icons()
            return 0
        if msg == WM_APP_SHOW:
            self.app.request_show()
            return 0
        if msg == WM_APP_INTERVAL:
            user32.KillTimer(hwnd, 1)
            user32.SetTimer(hwnd, 1, max(30, self.app.interval) * 1000, None)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.destroy()
            user32.KillTimer(hwnd, 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ===========================================================================
# 应用编排
# ===========================================================================
class TrayApp:
    def __init__(self, panel_only: bool = False):
        self.panel_only = panel_only
        self.theme_light = is_light_theme()
        self.interval = clamp_interval(load_config().get("interval", 60))
        self._readings: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._refreshing = False
        self._stop = False
        self.tray: Optional[Tray] = None
        self.panel: Optional[Panel] = None
        self._dirty = False
        self._show_requested = False
        self._full: dict[str, bool] = {}
        self.devices = load_saved_devices()
        self._seen = None
        self._asked = set()
        self._pending_add = None
        self._add_win = None
        self._last_poll = 0.0
        self._hotplug_requested = False
        self._toggle_requested = False
        self._tray_cmds: "queue.SimpleQueue[int]" = queue.SimpleQueue()

    # -- 数据 --------------------------------------------------------------
    def snapshot(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._readings)

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            res = read_saved_all(self.devices)
        except Exception:
            res = {}
        with self._lock:
            self._readings = res
        self._check_full(res)
        self._refreshing = False
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_REFRESH, 0, 0)

    def _check_full(self, res):
        """电量首次到 100% 时弹 Windows 通知；掉到 97% 以下重新布防。"""
        for key, r in res.items():
            if not r.ok or r.level is None or r.stale:
                continue
            if r.level >= 100:
                if not self._full.get(key):
                    self._full[key] = True
                    if self.tray:
                        try:
                            self.tray.notify(f"{r.name} 已经充满电啦")
                        except Exception:
                            pass
            elif r.level < 97:
                self._full[key] = False

    # -- 交互 --------------------------------------------------------------
    def request_show(self):
        self._show_requested = True

    def request_toggle(self):
        self._toggle_requested = True

    def tray_command(self, cmd: int):
        """托盘线程只投递命令，Tk 相关操作统一在主线程执行。"""
        self._tray_cmds.put(cmd)

    def _run_tray_command(self, cmd: int):
        if cmd == TRAY_OPEN:
            self.panel.show()
            self.refresh()
        elif cmd == TRAY_REFRESH:
            self.refresh()
        elif cmd == TRAY_STARTUP:
            self.panel._toggle_startup()
        elif cmd == TRAY_QUIT:
            self.quit()

    def set_interval(self, sec) -> int:
        sec = clamp_interval(sec)
        if sec == self.interval:
            return sec
        self.interval = sec
        cfg = load_config()
        cfg["interval"] = sec
        save_config(cfg)
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_INTERVAL, 0, 0)
        return sec

    def on_theme_changed(self):
        self.theme_light = is_light_theme()
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_REFRESH, 0, 0)

    def _poll_hotplug(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_poll < 10.0:
            return
        self._last_poll = now
        try:
            present = {(d.vid, d.pid) for d in enumerate_hid()}
        except Exception:
            return
        if self._seen is None:
            self._seen = present
            self._asked = set(present)
            return
        new = present - self._seen
        self._seen = present
        self._asked &= present
        for vid, pid in sorted(new):
            if (vid, pid) in self._asked:
                continue
            if any(d["vid"] == vid and d["pid"] == pid
                   for d in self.devices):
                continue
            self._asked.add((vid, pid))
            self._pending_add = (vid, pid)
            break

    def _show_add(self, vid: int, pid: int):
        try:
            ifaces = enumerate_hid()
            r = read_saved_device({"vid": vid, "pid": pid, "name": ""},
                                  ifaces)
            name = r.name or f"{vid:04X}:{pid:04X}"
            level = r.level if r.ok else None
            self._add_win = AddDeviceWindow(self, vid, pid, name, level)
        except Exception:
            self._add_win = None

    def rename_device(self, key: str, name: str):
        for d in self.devices:
            if device_key(d["vid"], d["pid"]) == key:
                d["name"] = name
                save_saved_devices(self.devices)
                break

    def add_device(self, vid: int, pid: int, name: str):
        if any(d["vid"] == vid and d["pid"] == pid for d in self.devices):
            return
        self.devices.append({"vid": vid, "pid": pid, "name": name})
        save_saved_devices(self.devices)
        self.refresh()

    def remove_device(self, key: str):
        removed = next((d for d in self.devices
                        if device_key(d["vid"], d["pid"]) == key), None)
        self.devices = [d for d in self.devices
                        if device_key(d["vid"], d["pid"]) != key]
        save_saved_devices(self.devices)
        if removed is not None:
            cfg = load_config()
            protocols = cfg.get("protocols")
            if isinstance(protocols, dict):
                protocols.pop(f"{removed['vid']:04X}:{removed['pid']:04X}",
                              None)
            last = cfg.get("last")
            if isinstance(last, dict):
                last.pop(key, None)
            save_config(cfg)
        with self._lock:
            self._readings.pop(key, None)
        self.refresh()

    def quit(self):
        self._stop = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_CLOSE, 0, 0)
        if self.panel:
            try:
                self.panel._close_help()
                self.panel._withdraw()
            except Exception:
                pass
        try:
            self.root.quit()
        except Exception:
            pass

    # -- 主循环 ------------------------------------------------------------
    def run(self):
        migrate_startup_link()
        self.panel = Panel(self)
        self.root = self.panel.root
        if not self.panel_only:
            self.tray = Tray(self)
            self.tray.start()
        self.refresh()

        def tick():
            if self._hotplug_requested:
                self._hotplug_requested = False
                self._poll_hotplug(force=True)
            else:
                self._poll_hotplug()
            if self._pending_add and self._add_win is None:
                vid, pid = self._pending_add
                self._pending_add = None
                self._show_add(vid, pid)
            if self._show_requested:
                self._show_requested = False
                self.panel.show()
            if self._toggle_requested:
                self._toggle_requested = False
                if self.panel.visible:
                    self.panel.hide()
                elif time.time() - self.panel._dismissed_at > 0.4:
                    # 刚因点击托盘图标失焦收起的，这次点击就是“关”
                    self.panel.show()
                    self.refresh()
            while not self._tray_cmds.empty():
                try:
                    self._run_tray_command(self._tray_cmds.get_nowait())
                except Exception:
                    pass
            if self._dirty:
                self._dirty = False
                if self.root.winfo_viewable():
                    self.panel.refresh_view()
            if self._stop:
                self.root.quit()
                return
            self.root.after(50, tick)

        self.root.after(50, tick)
        if self.panel_only:
            self.panel.show()
        self.root.mainloop()
        try:
            if self.tray:
                self.tray.destroy()
        except Exception:
            pass
        os._exit(0)


APP_ID = "2.4GDevice.BatteryPanel"
APP_DISPLAY_NAME = "2.4G"


def register_app_identity():
    """让 Windows 通知显示为 2.4G，而不是 Python。"""
    try:
        import winreg
        path = r"Software\Classes\AppUserModelId\%s" % APP_ID
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ,
                              APP_DISPLAY_NAME)
    except Exception:
        pass
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wt.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def run_gui(panel_only: bool = False) -> int:
    global UI_SCALE
    register_app_identity()
    user32.SetProcessDPIAware()
    try:
        user32.GetDpiForSystem.restype = wt.UINT
        UI_SCALE = max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        UI_SCALE = 1.0
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "Local\\BatteryTrayDSH_Mutex")
    if ctypes.get_last_error() == 183 and mutex:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(Tray.CLASS_NAME, None)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_SHOW, 0, 0)
        return 0
    app = TrayApp(panel_only=panel_only)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

