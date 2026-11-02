#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外设电量 — Windows 10/11 无线外设电量托盘工具

纯标准库 + ctypes + tkinter，零第三方依赖。

用法：
    python my_devices.py                   # 托盘 + 电量面板
    python my_devices.py --once            # 读一次并打印
    python my_devices.py --list            # 列出所有 HID 接口
    python my_devices.py --panel           # 只开面板（不常驻托盘）
    python my_devices.py --install         # 创建开机自启 + 桌面入口
    python my_devices.py --import 文件.json  # 导入别人分享的协议（并添加设备）
    python my_devices.py --export 文件.json  # 导出已保存的协议，分享给同款设备用户
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
import re
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
                # 鼠标 / 键盘集合拒绝读权限（如雷蛇接收器），只要写权限仍能收发 Feature
                self.h = _open_path(path, GENERIC_WRITE)
                if self.h and self.h != INVALID_HANDLE_VALUE:
                    self.rh = _open_path(path, GENERIC_READ, overlapped=True)
                    if not self.rh or self.rh == INVALID_HANDLE_VALUE:
                        self.rh = 0
                    return
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
    kind: str = "hid"        # hid = 2.4G 接收器；bluetooth = 蓝牙设备
    device_type: str = ""    # mouse / keyboard / headset / gamepad（适配器知道时）

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
# 蓝牙电量：读 Windows 自己维护的设备属性（与“设置 > 蓝牙和其他设备”一致）
# ===========================================================================
class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.ULONG)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", GUID),
                ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wt.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay",
                                        "wHour", "wMinute", "wSecond",
                                        "wMilliseconds")]


class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("fReturnAuthenticated", wt.BOOL),
                ("fReturnRemembered", wt.BOOL), ("fReturnUnknown", wt.BOOL),
                ("fReturnConnected", wt.BOOL), ("fIssueInquiry", wt.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", wt.HANDLE)]


class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("Address", ctypes.c_ulonglong),
                ("ulClassofDevice", wt.ULONG), ("fConnected", wt.BOOL),
                ("fRemembered", wt.BOOL), ("fAuthenticated", wt.BOOL),
                ("stLastSeen", SYSTEMTIME), ("stLastUsed", SYSTEMTIME),
                ("szName", wt.WCHAR * 248)]


DIGCF_ALLCLASSES = 0x04
DEVPROP_TYPE_BYTE = 0x03
DEVPROP_TYPE_GUID = 0x0D
DEVPROP_TYPE_STRING = 0x12
DEVPKEY_BLUETOOTH_BATTERY = ("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}", 2)
DEVPKEY_NAME = ("{B725F130-47EF-101A-A5F1-02608C9EEBAC}", 10)
DEVPKEY_DEVICE_CONTAINER_ID = ("{8C7ED206-3F8A-4827-B3AB-AE9E1FAEFC6C}", 2)
BT_ENUMERATORS = ("BTHENUM\\", "BTHLE\\", "BTHLEDEVICE\\", "BTHHFENUM\\")

setupapi.SetupDiEnumDeviceInfo.argtypes = [wt.HANDLE, wt.DWORD,
                                           ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
    ctypes.POINTER(wt.ULONG), ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), wt.DWORD]
# 第二个参数是 SP_DEVINFO_DATA；HID 枚举那边传的是接口数据，统一放宽成指针
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD, ctypes.POINTER(wt.DWORD)]


def _devprop(hdev, info, key) -> Optional[tuple]:
    """读一个设备属性，返回 (类型, 原始字节)。"""
    k = DEVPROPKEY()
    k.fmtid = GUID(key[0])
    k.pid = key[1]
    typ = wt.ULONG(0)
    need = wt.DWORD(0)
    buf = ctypes.create_string_buffer(1024)
    if not setupapi.SetupDiGetDevicePropertyW(
            hdev, ctypes.byref(info), ctypes.byref(k), ctypes.byref(typ),
            buf, 1024, ctypes.byref(need), 0):
        return None
    return typ.value, buf.raw[:need.value]


def _bt_nodes() -> list:
    """所有带电量属性的蓝牙设备节点：[{inst, level, name, container}]。"""
    return _bt_scan()[0]


def _bt_scan() -> tuple:
    """一次枚举拿两样：(带电量属性的蓝牙节点, 苹果耳机节点 [{inst, name}])。
    苹果耳机认 Device ID 里的 VID&0001004C（AirPods、Beats 都是），
    或者名字里带 AirPods / Beats。"""
    hdev = setupapi.SetupDiGetClassDevsW(None, None, None,
                                         DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        return [], []
    out, apple = [], []
    try:
        i = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(info)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, i, ctypes.byref(info)):
                break
            i += 1
            inst = ctypes.create_unicode_buffer(512)
            if not setupapi.SetupDiGetDeviceInstanceIdW(
                    hdev, ctypes.byref(info), inst, 512, None):
                continue
            if not inst.value.upper().startswith(BT_ENUMERATORS):
                continue
            name = _devprop(hdev, info, DEVPKEY_NAME)
            name = (name[1].decode("utf-16-le", "ignore").split("\0")[0]
                    if name and name[0] == DEVPROP_TYPE_STRING else "")
            if is_apple_audio(inst.value, name):
                apple.append({"inst": inst.value, "name": name})
            bat = _devprop(hdev, info, DEVPKEY_BLUETOOTH_BATTERY)
            if not bat or bat[0] != DEVPROP_TYPE_BYTE or not bat[1]:
                continue
            cont = _devprop(hdev, info, DEVPKEY_DEVICE_CONTAINER_ID)
            out.append({
                "inst": inst.value,
                "level": bat[1][0],
                "name": name,
                "container": (cont[1].hex()
                              if cont and cont[0] == DEVPROP_TYPE_GUID else ""),
            })
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out, apple


def _bt_connected() -> dict:
    """{蓝牙地址(12 位十六进制): 是否已连接}，来自 Windows 已配对设备列表。"""
    try:
        bt = ctypes.WinDLL("bthprops.cpl")
        bt.BluetoothFindFirstDevice.restype = ctypes.c_void_p
        bt.BluetoothFindFirstDevice.argtypes = [
            ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS),
            ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
        bt.BluetoothFindNextDevice.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
        bt.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
    except Exception:
        return {}
    p = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    p.dwSize = ctypes.sizeof(p)
    p.fReturnAuthenticated = p.fReturnRemembered = p.fReturnConnected = True
    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    h = bt.BluetoothFindFirstDevice(ctypes.byref(p), ctypes.byref(info))
    if not h:
        return {}
    out = {}
    try:
        while True:
            out["%012X" % info.Address] = bool(info.fConnected)
            info.dwSize = ctypes.sizeof(info)
            if not bt.BluetoothFindNextDevice(h, ctypes.byref(info)):
                break
    finally:
        bt.BluetoothFindDeviceClose(h)
    return out


def _bt_address(inst: str) -> str:
    """从设备实例 ID 里取蓝牙地址：最后一段独立的 12 位十六进制
    （BTHENUM 的 ..._001A7DDA7113_C00000000、BTHLE 的 DEV_C1D2E3F4A5B6）。"""
    found = re.findall(r"(?<![0-9A-F])([0-9A-F]{12})(?![0-9A-F])",
                       inst.upper())
    return found[-1] if found else ""


def _bt_merge(nodes: list, connected: dict) -> dict[str, Reading]:
    """同一台设备常有多个节点（音频/免提/GATT 服务），按容器 ID 合并成一张卡。
    Windows 确认已断开的设备不显示（属性里留的是断开前的旧电量）。"""
    groups: dict[str, list] = {}
    for n in nodes:
        gid = n.get("container") or _bt_address(n["inst"]) or n["inst"]
        groups.setdefault(gid, []).append(n)
    out: dict[str, Reading] = {}
    for gid, ns in groups.items():
        addr = next((a for a in (_bt_address(n["inst"]) for n in ns) if a), "")
        conn = connected.get(addr)
        if conn is False:
            continue
        level = next((n["level"] for n in ns if 0 <= n["level"] <= 100), None)
        if level is None:
            continue
        name = next((n["name"] for n in ns if n["name"]), "") or "蓝牙设备"
        key = "bt_" + (addr or gid[:12]).lower()
        r = Reading(ok=True, level=level, status="bluetooth", source="蓝牙",
                    detail="已连接" if conn else "", name=name)
        r.kind = "bluetooth"
        out[key] = r
    return out


def read_bluetooth_all() -> dict[str, Reading]:
    cfg = load_config()
    if not cfg.get("bluetooth", True):
        return {}
    hidden = set(cfg.get("bt_hidden") or [])
    try:
        nodes, apple = _bt_scan()
        connected = _bt_connected()
        res = _bt_merge(nodes, connected)
    except Exception:
        return {}
    try:
        res.update(read_airpods(apple, connected, res))
    except Exception:
        pass
    names = custom_names()
    out = {}
    for key, r in res.items():
        if key in hidden:
            continue
        r.name = names.get(key) or r.name
        out[key] = r
    return out


# ===========================================================================
# AirPods / Beats：Windows 不给苹果耳机电量属性（系统设置里也看不到电量），
# 但耳机自己一直在发 BLE 广播（苹果厂商数据 0x004C，类型 0x07「接近配对」），
# 里面带左耳、右耳、充电盒电量和充电状态。这里只被动收听广播，不连接、不写入。
# 字段布局参考 AirPodsDesktop（SpriteOvO）和 OpenPods 的分析。
# 防止显示旁边别人的耳机：只给 Windows 里已配对且已连接的苹果耳机找广播，
# 广播里的型号要和配对设备 Device ID 的 PID 一致，同型号有多副时取信号最强的。
# ===========================================================================
APPLE_VID = 0x004C
APPLE_AUDIO_NAME = re.compile(r"airpods|beats", re.I)
APPLE_DEVICE_ID = re.compile(r"VID&0001004C_PID&([0-9A-F]{4})")
AIRPODS_MAX_AGE = 90          # 秒：超过这么久没收到广播就不显示
AIRPODS_MIN_RSSI = -75        # 不知道型号时，只认离电脑足够近的广播


def is_apple_audio(inst: str, name: str) -> bool:
    return bool(APPLE_DEVICE_ID.search(inst.upper())
                or APPLE_AUDIO_NAME.search(name or ""))


def parse_airpods(data: bytes) -> Optional[dict]:
    """解析「接近配对」广播（厂商数据去掉 0x004C 之后的部分）。
    [0]=0x07 [1]=剩余长度 [3:5]=型号(小端) [5]=状态 [6]=两只耳机电量
    [7]=低 4 位充电盒电量 + 高位充电标志。电量 0~10 对应 0~100%，15 = 不在线。
    状态 0x20 置位表示这条广播来自左耳，此时 [6] 低 4 位是左耳。"""
    if len(data) < 9 or data[0] != 0x07 or data[1] < 7:
        return None
    model = data[3] | data[4] << 8
    status, pods, flags = data[5], data[6], data[7]
    curr, other = pods & 0x0F, pods >> 4
    curr_chg, other_chg = bool(flags & 0x10), bool(flags & 0x20)
    if status & 0x20:
        left, right, lc, rc = curr, other, curr_chg, other_chg
    else:
        left, right, lc, rc = other, curr, other_chg, curr_chg

    def pct(v):
        return v * 10 if v <= 10 else None
    return {"model": model,
            "left": pct(left), "right": pct(right), "case": pct(flags & 0x0F),
            "left_charging": lc, "right_charging": rc,
            "case_charging": bool(flags & 0x40)}


def airpods_reading(p: dict, name: str) -> Optional[Reading]:
    """卡片电量取两只耳机里低的那只，细节里列出左 / 右 / 充电盒。"""
    pods = [(p["left"], p["left_charging"]), (p["right"], p["right_charging"])]
    pods = [(v, c) for v, c in pods if v is not None]
    if not pods:
        return None
    parts = []
    if p["left"] is not None and p["right"] is not None:
        parts = [f"左 {p['left']}%", f"右 {p['right']}%"]
    if p["case"] is not None:
        parts.append(f"盒 {p['case']}%")
    charging = all(c for _, c in pods)
    r = Reading(ok=True, level=min(v for v, _ in pods),
                status="charging" if charging else "bluetooth",
                source="蓝牙广播", detail=" · ".join(parts), name=name)
    r.kind, r.device_type = "bluetooth", "headset"
    return r


def _airpods_merge(apple: list, connected: dict, adverts: dict,
                   have: dict, now: float) -> dict[str, Reading]:
    """apple：配对节点 [{inst, name}]；adverts：{BLE 地址: (时间, 信号, 解析结果)}。"""
    groups: dict[str, dict] = {}
    for n in apple:
        addr = _bt_address(n["inst"])
        if not addr:
            continue
        g = groups.setdefault(addr, {"name": "", "pid": None})
        m = APPLE_DEVICE_ID.search(n["inst"].upper())
        if m:
            g["pid"] = int(m.group(1), 16)
        # 设备节点（BTHENUM\DEV_…）的名字就是用户看到的名字；没有就取最短的服务节点名
        if not n["name"]:
            continue
        if n["inst"].upper().startswith("BTHENUM\\DEV_"):
            g["dev_name"] = n["name"]
        elif not g["name"] or len(n["name"]) < len(g["name"]):
            g["name"] = n["name"]
    out: dict[str, Reading] = {}
    for addr, g in groups.items():
        key = "bt_" + addr.lower()
        if key in have or connected.get(addr) is False:
            continue
        best = None
        for ts, rssi, p in adverts.values():
            if now - ts > AIRPODS_MAX_AGE:
                continue
            if g["pid"] is not None and p["model"] != g["pid"]:
                continue
            if g["pid"] is None and rssi < AIRPODS_MIN_RSSI:
                continue
            if best is None or rssi > best[0]:
                best = (rssi, p)
        if best is None:
            continue
        r = airpods_reading(best[1],
                            g.get("dev_name") or g["name"] or "AirPods")
        if r is not None:
            out[key] = r
    return out


# --- WinRT 小工具：用 ctypes 直接调 COM 虚表，不依赖第三方库 ----------------
_HR = ctypes.c_long
E_NOINTERFACE = -2147467262
IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"
IID_IAGILE = "{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}"
IID_BLE_WATCHER = "{A6AC336F-F3D3-4297-8D6C-C81EA6623F40}"
IID_BLE_ADV = "{066FB2B7-33D1-4E7D-8367-CF81D0F79653}"
IID_BLE_MFR = "{912DBA18-6963-4533-B061-4694DAFB34E5}"
IID_BUFFER_BYTES = "{905A0FEF-BC53-11DF-8C49-001E4FC686DA}"
# TypedEventHandler<BluetoothLEAdvertisementWatcher, ReceivedEventArgs>，
# 按 WinRT 参数化接口规则（UUID v5）算出来的
IID_BLE_RECEIVED = "{90EB4ECA-D465-5EA0-A61C-033C8C5ECEF2}"
BLE_WATCHER_CLASS = "Windows.Devices.Bluetooth.Advertisement." \
                    "BluetoothLEAdvertisementWatcher"
_combase = None


def _winrt():
    global _combase
    if _combase is None:
        _combase = False
        try:
            dll = ctypes.WinDLL("combase")
            dll.RoInitialize.argtypes = [ctypes.c_int]
            dll.WindowsCreateString.argtypes = [
                wt.LPCWSTR, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
            dll.WindowsDeleteString.argtypes = [ctypes.c_void_p]
            dll.RoActivateInstance.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            dll.RoGetActivationFactory.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p)]
            _combase = dll
        except Exception:
            pass
    return _combase or None


def _vt(obj, idx: int, *argtypes):
    """obj 虚表里第 idx 个方法（IInspectable 自己占 0~5，接口方法从 6 开始）。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(_HR, ctypes.c_void_p, *argtypes)(vtbl[idx])
    return lambda *a: fn(obj, *a)


def _release(obj):
    if obj:
        _vt(obj, 2)()


def _qi(obj, iid: str):
    out = ctypes.c_void_p()
    g = GUID(iid)
    hr = _vt(obj, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(g), ctypes.byref(out))
    return out.value if hr >= 0 and out.value else None


def _hstring(dll, s: str):
    h = ctypes.c_void_p()
    if dll.WindowsCreateString(s, len(s), ctypes.byref(h)) < 0:
        return None
    return h


def _activate(cls: str, iid: str):
    """RoActivateInstance + QueryInterface，失败返回 None。"""
    dll = _winrt()
    if dll is None:
        return None
    h = _hstring(dll, cls)
    if h is None:
        return None
    try:
        insp = ctypes.c_void_p()
        if dll.RoActivateInstance(h, ctypes.byref(insp)) < 0 or not insp.value:
            return None
    finally:
        dll.WindowsDeleteString(h)
    try:
        return _qi(insp.value, iid)
    finally:
        _release(insp.value)


def _factory(cls: str, iid: str):
    dll = _winrt()
    if dll is None:
        return None
    h = _hstring(dll, cls)
    if h is None:
        return None
    try:
        out = ctypes.c_void_p()
        g = GUID(iid)
        if dll.RoGetActivationFactory(h, ctypes.byref(g),
                                      ctypes.byref(out)) < 0:
            return None
        return out.value
    finally:
        dll.WindowsDeleteString(h)


def _buffer_bytes(buf) -> bytes:
    """IBuffer → bytes（经 IBufferByteAccess 拿内存地址）。"""
    n = ctypes.c_uint32()
    if _vt(buf, 7, ctypes.POINTER(ctypes.c_uint32))(ctypes.byref(n)) < 0:
        return b""
    acc = _qi(buf, IID_BUFFER_BYTES)
    if not acc:
        return b""
    try:
        p = ctypes.c_void_p()
        if _vt(acc, 3, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(p)) < 0:
            return b""
        return ctypes.string_at(p.value, n.value) if p.value else b""
    finally:
        _release(acc)


def ble_apple_data(adv) -> list:
    """IBluetoothLEAdvertisement 里所有苹果厂商数据（每段是 bytes）。"""
    view = ctypes.c_void_p()
    hr = _vt(adv, 13, ctypes.c_uint16, ctypes.POINTER(ctypes.c_void_p))(
        APPLE_VID, ctypes.byref(view))
    if hr < 0 or not view.value:
        return []
    out = []
    try:
        n = ctypes.c_uint32()
        _vt(view.value, 7, ctypes.POINTER(ctypes.c_uint32))(ctypes.byref(n))
        for i in range(min(n.value, 4)):
            item = ctypes.c_void_p()
            if _vt(view.value, 6, ctypes.c_uint32,
                   ctypes.POINTER(ctypes.c_void_p))(
                    i, ctypes.byref(item)) < 0 or not item.value:
                continue
            try:
                buf = ctypes.c_void_p()
                if _vt(item.value, 8, ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.byref(buf)) >= 0 and buf.value:
                    try:
                        out.append(_buffer_bytes(buf.value))
                    finally:
                        _release(buf.value)
            finally:
                _release(item.value)
    finally:
        _release(view.value)
    return out


_QI_T = ctypes.WINFUNCTYPE(_HR, ctypes.c_void_p, ctypes.POINTER(GUID),
                           ctypes.POINTER(ctypes.c_void_p))
_REF_T = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_INVOKE_T = ctypes.WINFUNCTYPE(_HR, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p)


class _DelegateVtbl(ctypes.Structure):
    _fields_ = [("qi", _QI_T), ("addref", _REF_T), ("release", _REF_T),
                ("invoke", _INVOKE_T)]


class _DelegateObj(ctypes.Structure):
    _fields_ = [("vtbl", ctypes.POINTER(_DelegateVtbl))]


class _Delegate:
    """手搓的 WinRT 事件回调对象。对象一直留在内存里，引用计数固定返回 1。"""

    def __init__(self, iid: str, fn):
        self._ok = {bytes(GUID(x)) for x in (IID_IUNKNOWN, IID_IAGILE, iid)}
        self._fn = fn
        self._vtbl = _DelegateVtbl(_QI_T(self._qi), _REF_T(self._ref),
                                   _REF_T(self._ref), _INVOKE_T(self._invoke))
        self._obj = _DelegateObj(ctypes.pointer(self._vtbl))
        self.ptr = ctypes.addressof(self._obj)

    def _qi(self, this, riid, out):
        if bytes(riid.contents) in self._ok:
            out[0] = this
            return 0
        out[0] = None
        return E_NOINTERFACE

    def _ref(self, this):
        return 1

    def _invoke(self, this, sender, args):
        try:
            self._fn(args)
        except Exception:
            pass
        return 0


class AirPodsListener:
    """后台被动收听 BLE 广播，只留苹果「接近配对」那一种。
    有苹果耳机连着才开始听；5 分钟没人要数据就停掉，省电。"""
    IDLE_STOP = 300

    def __init__(self):
        self.adverts: dict = {}
        self.lock = threading.Lock()
        self.wanted = 0.0
        self.started = False
        self.error = ""

    def want(self) -> bool:
        """标记“现在需要数据”；返回 True 表示这次才刚开始听。"""
        self.wanted = time.time()
        if self.started:
            return False
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.adverts)

    def _on_received(self, args):
        adv = ctypes.c_void_p()
        if _vt(args, 10, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(adv)) < 0 or not adv.value:
            return
        try:
            datas = ble_apple_data(adv.value)
        finally:
            _release(adv.value)
        hits = [p for p in map(parse_airpods, datas) if p]
        if not hits:
            return
        rssi = ctypes.c_int16()
        addr = ctypes.c_uint64()
        _vt(args, 6, ctypes.POINTER(ctypes.c_int16))(ctypes.byref(rssi))
        _vt(args, 7, ctypes.POINTER(ctypes.c_uint64))(ctypes.byref(addr))
        now = time.time()
        with self.lock:
            self.adverts[addr.value] = (now, rssi.value, hits[0])
            if len(self.adverts) > 32:
                for k, v in list(self.adverts.items()):
                    if now - v[0] > AIRPODS_MAX_AGE:
                        del self.adverts[k]

    def _run(self):
        dll = _winrt()
        if dll is None:
            self.error = "没有 WinRT"
            return
        dll.RoInitialize(1)       # 多线程套间：回调直接在系统线程池里来
        w = _activate(BLE_WATCHER_CLASS, IID_BLE_WATCHER)
        if not w:
            self.error = "无法创建蓝牙广播监听"
            return
        self._delegate = _Delegate(IID_BLE_RECEIVED, self._on_received)
        token = ctypes.c_int64()
        if _vt(w, 19, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))(
                self._delegate.ptr, ctypes.byref(token)) < 0:
            self.error = "无法订阅蓝牙广播"
            return
        scanning = False
        while True:
            idle = time.time() - self.wanted > self.IDLE_STOP
            st = ctypes.c_int(-1)
            _vt(w, 10, ctypes.POINTER(ctypes.c_int))(ctypes.byref(st))
            if idle and scanning:
                _vt(w, 18)()                  # Stop
                scanning = False
            elif not idle and st.value in (0, 3, 4):
                # 0 = 刚创建、3 = 已停止、4 = 中断（比如蓝牙被关掉），重新开始
                scanning = _vt(w, 17)() >= 0  # Start
            time.sleep(5)


AIRPODS = AirPodsListener()


def read_airpods(apple: list, connected: dict, have: dict) -> dict:
    """已连接的苹果耳机 → 从广播里找电量。第一次开始听时等几秒，
    耳机通常每秒都会广播，等到了就不用多等一轮刷新。"""
    if not apple:
        return {}
    addrs = {_bt_address(n["inst"]) for n in apple}
    if not any(connected.get(a) is not False for a in addrs if a):
        return {}
    if AIRPODS.want():
        for _ in range(8):
            time.sleep(0.5)
            if AIRPODS.snapshot():
                break
    return _airpods_merge(apple, connected, AIRPODS.snapshot(), have,
                          time.time())


# ===========================================================================
# Xbox 兼容手柄（XInput）：Xbox 模式的手柄没有厂商 HID 通道，只能问 XInput。
# XInputGetBatteryInformation 只给四档（满 / 中 / 低 / 空），按档位中间值显示并注明。
# 参考 Voltide 的 XInputProvider。
# ===========================================================================
_XINPUT = None
XINPUT_LEVEL = {3: 100, 2: 50, 1: 20, 0: 5}


def _xinput():
    global _XINPUT
    if _XINPUT is None:
        _XINPUT = False
        for name in ("XInput1_4", "XInput1_3", "XInput9_1_0"):
            try:
                _XINPUT = ctypes.WinDLL(name)
                break
            except OSError:
                continue
    return _XINPUT or None


def read_xinput_all() -> dict[str, Reading]:
    if sys.platform != "win32":
        return {}
    dll = _xinput()
    if dll is None:
        return {}
    hidden = set(load_config().get("bt_hidden") or [])
    names = custom_names()
    out = {}
    for slot in range(4):
        key = f"xi_{slot}"
        if key in hidden:
            continue
        state = ctypes.create_string_buffer(16)
        try:
            if dll.XInputGetState(slot, state) != 0:
                continue
            info = ctypes.create_string_buffer(2)
            if dll.XInputGetBatteryInformation(slot, 0, info) != 0:
                continue
        except Exception:
            continue
        btype, blevel = info.raw[0], info.raw[1]
        if btype == 0:                      # 已断开
            continue
        name = names.get(key) or f"Xbox 手柄 {slot + 1}"
        if btype == 1:                      # 有线：插着线就当充电
            r = Reading(ok=True, level=100, status="charging",
                        source="XInput", detail="XInput 有线")
        elif blevel in XINPUT_LEVEL:
            r = Reading(ok=True, level=XINPUT_LEVEL[blevel],
                        status="discharging", source="XInput",
                        detail="XInput 四档，约值")
        else:
            continue
        r.name, r.kind, r.device_type = name, "gamepad", "gamepad"
        out[key] = r
    return out


# ===========================================================================
# CPU / GPU 温度
#   GPU：D3DKMT 适配器性能数据（任务管理器同源，WDDM 2.7+ 驱动，无需管理员）
#   CPU：LibreHardwareMonitor / OpenHardwareMonitor 在运行时读它们的传感器（准确）；
#        否则退回系统“热区”计数器（ACPI，近似值，台式机常读不到）
# ===========================================================================
class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class D3DKMT_ADAPTERINFO(ctypes.Structure):
    _fields_ = [("hAdapter", wt.UINT), ("AdapterLuid", LUID),
                ("NumOfSources", wt.ULONG),
                ("bPrecisePresentRegionsPreferred", wt.BOOL)]


class D3DKMT_ENUMADAPTERS2(ctypes.Structure):
    _fields_ = [("NumAdapters", wt.ULONG),
                ("pAdapters", ctypes.POINTER(D3DKMT_ADAPTERINFO))]


class D3DKMT_QUERYADAPTERINFO(ctypes.Structure):
    _fields_ = [("hAdapter", wt.UINT), ("Type", ctypes.c_int),
                ("pPrivateDriverData", ctypes.c_void_p),
                ("PrivateDriverDataSize", wt.UINT)]


class D3DKMT_ADAPTER_PERFDATA(ctypes.Structure):
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint32),
                ("MemoryFrequency", ctypes.c_ulonglong),
                ("MaxMemoryFrequency", ctypes.c_ulonglong),
                ("MaxMemoryFrequencyOC", ctypes.c_ulonglong),
                ("MemoryBandwidth", ctypes.c_ulonglong),
                ("PCIEBandwidth", ctypes.c_ulonglong),
                ("FanRPM", wt.ULONG), ("Power", wt.ULONG),
                ("Temperature", wt.ULONG),        # 0.1 °C
                ("PowerStateOverride", ctypes.c_ubyte)]


class D3DKMT_ADAPTERADDRESS(ctypes.Structure):
    _fields_ = [("BusNumber", wt.UINT), ("DeviceNumber", wt.UINT),
                ("FunctionNumber", wt.UINT)]


class D3DKMT_ADAPTERREGISTRYINFO(ctypes.Structure):
    _fields_ = [("AdapterString", wt.WCHAR * 260),
                ("BiosString", wt.WCHAR * 260),
                ("DacType", wt.WCHAR * 260),
                ("ChipType", wt.WCHAR * 260)]


class D3DKMT_CLOSEADAPTER(ctypes.Structure):
    _fields_ = [("hAdapter", wt.UINT)]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("longValue", wt.LONG), ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong),
                    ("WideStringValue", ctypes.c_wchar_p)]
    _fields_ = [("CStatus", wt.DWORD), ("u", _U)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p),
                ("FmtValue", PDH_FMT_COUNTERVALUE)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


KMTQAITYPE_ADAPTERADDRESS = 6
KMTQAITYPE_ADAPTERREGISTRYINFO = 8
KMTQAITYPE_ADAPTERPERFDATA = 62
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
HW_MONITORS = ("librehardwaremonitor.exe", "openhardwaremonitor.exe")


def temp_rgb(value: Optional[float], c: dict) -> tuple:
    if value is None:
        return c["dim"]
    if value < 70:
        return c["ok"]
    if value < 85:
        return c["warn"]
    return c["bad"]


def gpu_temperatures() -> list:
    """[{name, value(°C)}]；驱动不提供温度的适配器（核显、基本显示）跳过。"""
    try:
        enum2 = gdi32.D3DKMTEnumAdapters2
        query = gdi32.D3DKMTQueryAdapterInfo
        close = gdi32.D3DKMTCloseAdapter
    except AttributeError:
        return []
    enum2.restype = query.restype = close.restype = ctypes.c_long
    enum2.argtypes = [ctypes.POINTER(D3DKMT_ENUMADAPTERS2)]
    query.argtypes = [ctypes.POINTER(D3DKMT_QUERYADAPTERINFO)]
    close.argtypes = [ctypes.POINTER(D3DKMT_CLOSEADAPTER)]
    ea = D3DKMT_ENUMADAPTERS2()
    if enum2(ctypes.byref(ea)) != 0 or not ea.NumAdapters:
        return []
    arr = (D3DKMT_ADAPTERINFO * ea.NumAdapters)()
    ea.pAdapters = ctypes.cast(arr, ctypes.POINTER(D3DKMT_ADAPTERINFO))
    if enum2(ctypes.byref(ea)) != 0:
        return []
    out = []
    for a in arr[:ea.NumAdapters]:
        try:
            perf = D3DKMT_ADAPTER_PERFDATA()
            q = D3DKMT_QUERYADAPTERINFO(
                a.hAdapter, KMTQAITYPE_ADAPTERPERFDATA,
                ctypes.cast(ctypes.byref(perf), ctypes.c_void_p),
                ctypes.sizeof(perf))
            if query(ctypes.byref(q)) != 0 or not 0 < perf.Temperature < 1500:
                continue
            reg = D3DKMT_ADAPTERREGISTRYINFO()
            q = D3DKMT_QUERYADAPTERINFO(
                a.hAdapter, KMTQAITYPE_ADAPTERREGISTRYINFO,
                ctypes.cast(ctypes.byref(reg), ctypes.c_void_p),
                ctypes.sizeof(reg))
            name = reg.AdapterString if query(ctypes.byref(q)) == 0 else ""
            addr = D3DKMT_ADAPTERADDRESS()
            q = D3DKMT_QUERYADAPTERINFO(
                a.hAdapter, KMTQAITYPE_ADAPTERADDRESS,
                ctypes.cast(ctypes.byref(addr), ctypes.c_void_p),
                ctypes.sizeof(addr))
            pci = ((addr.BusNumber, addr.DeviceNumber, addr.FunctionNumber)
                   if query(ctypes.byref(q)) == 0 else None)
            out.append({"name": name or "GPU", "pci": pci,
                        "value": perf.Temperature / 10.0})
        finally:
            close(ctypes.byref(D3DKMT_CLOSEADAPTER(a.hAdapter)))
    return dedupe_gpus(out)


def dedupe_gpus(gpus: list) -> list:
    """同一块物理显卡可能被枚举两次（渲染 / 显示两种身份、远程桌面或虚拟显示器的
    影子适配器），温度完全一样。按 PCI 地址去重；拿不到地址时按“名称 + 温度”去重。"""
    out, seen = [], set()
    for g in gpus:
        key = (("pci",) + tuple(g["pci"]) if g.get("pci")
               else ("name", g.get("name"), g.get("value")))
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


def gpu_label(name: str, fallback: str) -> str:
    """温度格上的显卡标签：取厂商名（NVIDIA / AMD / Intel），认不出就用 GPU1 这类编号。"""
    n = (name or "").lower()
    for word, label in (("nvidia", "NVIDIA"), ("geforce", "NVIDIA"),
                        ("radeon", "AMD"), ("amd", "AMD"), ("intel", "Intel"),
                        ("arc", "Intel")):
        if word in n:
            return label
    return fallback


def _cpu_thermal_zone() -> Optional[float]:
    """系统热区温度（开尔文转摄氏），取各热区最高值；读不到返回 None。"""
    try:
        pdh = ctypes.WinDLL("pdh")
    except Exception:
        return None
    for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW,
               pdh.PdhCollectQueryData, pdh.PdhGetFormattedCounterArrayW,
               pdh.PdhCloseQuery):
        fn.restype = ctypes.c_ulong
    q = ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
        return None
    try:
        counter = ctypes.c_void_p()
        if pdh.PdhAddEnglishCounterW(
                q, ctypes.c_wchar_p(
                    "\\Thermal Zone Information(*)\\Temperature"),
                None, ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(q) != 0:
            return None
        size, count = wt.DWORD(0), wt.DWORD(0)
        rc = pdh.PdhGetFormattedCounterArrayW(
            counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count),
            None)
        if rc != PDH_MORE_DATA or not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(
                counter, PDH_FMT_DOUBLE, ctypes.byref(size),
                ctypes.byref(count), buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
        vals = [items[i].FmtValue.u.doubleValue - 273.15
                for i in range(count.value)
                if items[i].FmtValue.CStatus in (0, 1)]
        vals = [v for v in vals if 5 < v < 125]
        return max(vals) if vals else None
    finally:
        pdh.PdhCloseQuery(q)


def _hw_monitor_running() -> bool:
    """LibreHardwareMonitor / OpenHardwareMonitor 是否在运行（不起 PowerShell 白跑）。"""
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.Process32FirstW.argtypes = [wt.HANDLE,
                                         ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wt.HANDLE,
                                        ctypes.POINTER(PROCESSENTRY32W)]
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == INVALID_HANDLE_VALUE:
        return False
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() in HW_MONITORS:
                return True
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return False


_LHM_CACHE = {"at": 0.0, "data": []}
_LHM_PS = (
    "$r=@(); foreach($n in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor')"
    "{ try { $r += Get-CimInstance -Namespace $n -ClassName Sensor "
    "-Filter \"SensorType='Temperature'\" -ErrorAction Stop | "
    "Select-Object Name,Parent,Value } catch {} }; "
    "ConvertTo-Json -InputObject @($r) -Compress")


def _lhm_sensors() -> list:
    """LibreHardwareMonitor 发布到 WMI 的温度传感器（10 秒缓存）。"""
    now = time.time()
    if now - _LHM_CACHE["at"] < 10:
        return _LHM_CACHE["data"]
    data = []
    try:
        if _hw_monitor_running():
            ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
            out = subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-Command", _LHM_PS],
                capture_output=True, timeout=8,
                creationflags=CREATE_NO_WINDOW).stdout
            text = out.decode("utf-8", "ignore").strip()
            data = json.loads(text) if text else []
            if isinstance(data, dict):
                data = [data]
    except Exception:
        data = []
    _LHM_CACHE.update(at=now, data=data)
    return data


def _pick_lhm(sensors: list) -> tuple:
    """从传感器列表挑出 (CPU 温度, [GPU 温度…])。"""
    cpu_pref = ("cpu package", "core (tctl/tdie)", "tctl/tdie", "core (tctl)",
                "core average", "core max", "cpu core")
    cpus, gpus = {}, {}
    for s in sensors:
        try:
            name = str(s.get("Name") or "")
            parent = str(s.get("Parent") or "").lower()
            value = float(s.get("Value"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 5 < value < 125:
            continue
        if "cpu" in parent:
            cpus[name.lower()] = value
        elif parent.startswith("/gpu"):
            gpus.setdefault(parent, {})[name.lower()] = value
    cpu = next((cpus[k] for k in cpu_pref if k in cpus),
               max(cpus.values()) if cpus else None)
    gpu_list = []
    for parent, vals in gpus.items():
        v = vals.get("gpu core", max(vals.values()))
        gpu_list.append({"name": parent, "value": v})
    return cpu, gpu_list


def read_temperatures() -> dict:
    """{cpu: {value, approx} | None, gpus: [{name, value}], ts}。"""
    cpu, gpus, lhm_gpus = None, [], []
    try:
        lhm = _lhm_sensors()
        if lhm:
            v, lhm_gpus = _pick_lhm(lhm)
            if v is not None:
                cpu = {"value": v, "approx": False}
    except Exception:
        pass
    try:
        gpus = gpu_temperatures()
    except Exception:
        gpus = []
    if not gpus:
        gpus = lhm_gpus
    if cpu is None:
        try:
            v = _cpu_thermal_zone()
        except Exception:
            v = None
        if v is not None:
            cpu = {"value": v, "approx": True}
    return {"cpu": cpu, "gpus": gpus, "ts": time.time()}


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
                       status=("unplugged" if (has_device_protocol(vid, pid)
                                               or adapter_knows(vid, pid))
                               else "unknown_proto"),
                       vid=vid, pid=pid, detail="未连接")
    prod = next((d.product for d in cand if d.product), "")
    if not name:
        name = prod or f"{vid:04X}:{pid:04X}"
    # 读取顺序：标准 HID 电量 → 用户保存的协议（抓包 / 导入）→ 内置设备适配器。
    # 旧版自动保存的 "preset" 协议只作最后的兜底，有适配器认领时以适配器为准。
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
    kind = proto.get("kind", "captured") if proto else None
    adapter = ar = None
    if r is not None:
        if proto is None:
            # 标准 HID 能直接读：保存成协议，之后休眠也算“有协议”
            save_standard_protocol(vid, pid)
            proto = {"kind": "standard"}
    else:
        if kind in ("captured",):
            try:
                rr = _read_user_protocol(cand, proto)
                if rr and rr.ok:
                    r = rr
            except Exception:
                pass
        if r is None:
            ar, adapter = read_by_adapters(cand)
            if ar is not None and ar.ok:
                r = ar
        if r is None and kind == "preset":
            try:
                rr = _read_user_protocol(cand, proto)
                if rr and rr.ok:
                    r = rr
            except Exception:
                pass
    supported = proto is not None or adapter is not None
    src = (adapter and f"{adapter.brand} 协议") or protocol_source(proto)
    if r is None or not r.ok:
        err = ar.detail if (ar is not None and ar.status != "asleep") else ""
        if supported:
            status, detail = ("error" if err else "asleep"), \
                err or "设备休眠"
        elif not battery_channel_possible(cand):
            status, detail = "no_battery", "这个设备不向电脑提供电量信息"
        else:
            status, detail = "unknown_proto", "协议未知，无法读取电量"
        r = Reading(ok=False, present=True, name=name, status=status,
                    source=src if supported else "", detail=detail)
    r.name = name
    r.present = True
    if adapter is not None and not r.device_type:
        r.device_type = adapter.kind
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
                         "hid_known": r.hid_known,
                         "device_type": r.device_type}
            changed = True
            out[key] = r
            continue
        if ((not r.ok) and key in last
                and r.status not in ("unknown_proto", "unplugged",
                                     "no_battery")):
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
                out[key].device_type = (r.device_type
                                        or str(item.get("device_type", "")))
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
              "reply", "reply_id", "in_len", "match",
              # 内置协议 / 导入协议额外用到的字段
              "name", "usage_page", "tries", "wait_ms", "timeout_ms",
              "strict", "scale", "level_max", "source")
PROTO_KINDS = ("standard", "captured", "preset")


def save_user_protocol(vid: int, pid: int, proto: dict,
                       kind: str = "captured") -> None:
    cfg = load_config()
    protos = cfg.get("protocols")
    if not isinstance(protos, dict):
        protos = {}
    saved = {"kind": kind}
    for k in PROTO_KEYS:
        if k in proto:
            saved[k] = proto[k]
    saved["invert"] = bool(proto.get("invert", False))
    saved["requests"] = list(proto.get("requests", []))
    saved["offset"] = int(proto.get("offset", 2))
    protos[f"{vid:04X}:{pid:04X}"] = saved
    cfg["protocols"] = protos
    save_config(cfg)


# ---------------------------------------------------------------------------
# 内置设备适配器：每个品牌一个“适配器”，按精确的硬件指纹（VID / PID / usage page /
# 报告长度）认领设备，只发有出处的查询帧，回复必须对上回显和校验才采信。
#
# 方法与协议来源（都是 MIT 项目，或只取协议事实、自行实现）：
#   Voltide（sdparklab）          适配器架构；罗技、迈从、ROG、雷蛇、ATK、IMECOO、XInput
#   InphicBatteryTray（Tan-TanZi） 英菲克 IN10 被动上报、IN9 Feature 查询
#   FerrisPulse（Ferris-echo，GPL） 只参考协议文档：CompX 同号段隔离、雷柏遥测、宏碁、PixArt 引擎
#   VigilWatt / HaloBattery       写入安全规则、共用 VID 陷阱、交叉核对
#   2.4G-battery-viewer           ATK 64 字节协议、DARKRIM 杰理协议
# ---------------------------------------------------------------------------
def _jl_query(tlv: int) -> str:
    """杰理 JL_RCSP 查询帧（序号 0、TLV 长度 0），CRC16-CCITT-FALSE 大端。"""
    f = bytearray((0x00, 0xFE, 0x00, 0x10, 0x00, 0x02, 0x00, 0x00, 0x02, 0x00,
                   tlv, 0x00))
    crc = 0xFFFF
    for b in f[1:]:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    f += bytes((crc >> 8, crc & 0xFF))
    return " ".join(f"{b:02X}" for b in f)


# 声明式适配器（单帧问答 / 被动监听）。字段：
#   ids     [(VID, PID 或 None)]，None = 该 VID 下任意 PID（只用于品牌独占的 VID）
#   page / usage / lens   接口指纹；lens 里的长度默认必须相等，lens_min=True 时为下限
#   send    发送方式，按顺序尝试：out=WriteFile，setout=SET_REPORT(Output)，
#           feat=SET_REPORT(Feature)；空列表 = 只听不写
#   frames  要发的帧（含报告 ID），最后一帧之后等回复；csum 给出帧内校验和算法
#   reply   input / feature（reply_id 为 GET_FEATURE 的报告 ID）
#   expect  回复里必须相等的 [位置, 值]；nz 必须非零的位置；find 在回复里找特征串
#   oneof   [位置, [允许的值…]]
#   rcsum   回复的校验和；level=[位置, 倍率, 原始上限]；charge/full=[位置, [值…]]
#   mv      [位置, "be"/"le"] 电池毫伏（只显示）
DEVICE_SPECS = [
    {"id": "compx17", "brand": "ATK", "name": "ATK 无线鼠标", "kind": "mouse",
     "names": {(0x373B, 0x1278): "ATK A9 Air 大师+",
               (0x373B, 0x11D9): "ATK F1 Ultimate 2.0",
               (0x3554, 0xFB34): "IMECOO P1"},
     "ids": [(0x373B, None), (0x3554, 0xFB34)],
     "page": 0xFF02, "lens": {"in": 17, "out": 17},
     "send": ["setout", "out"], "frames": ["08 04"],
     "csum": {"kind": "const", "const": 0x4D, "from": 1, "to": 15, "at": 16},
     "reply": "input", "expect": [[0, 0x08], [1, 0x04], [2, 0x00]],
     "rcsum": {"kind": "const", "const": 0x4D, "from": 1, "to": 15, "at": 16},
     "level": [6], "charge": [7, [1]], "mv": [8, "be"], "timeout": 700,
     "verified": True, "source": "Voltide（ATK A9 Air、IMECOO P1 真机）· FerrisPulse"},
    {"id": "atk64", "brand": "ATK", "name": "ATK 无线鼠标", "kind": "mouse",
     "ids": [(0x373B, None)], "page": 0xFF05, "lens": {"out": 64},
     "send": ["out"], "frames": ["08 7D 72 02 00 01 07 01"],
     "reply": "input", "expect": [[0, 0x08], [1, 0x72], [5, 0x07]],
     "level": [7], "timeout": 600, "drain": True,
     "verified": True, "source": "2.4G-battery-viewer（373B:1216 真机）"},
    {"id": "atk_n9", "brand": "ATK", "name": "ATK N9 V2 Ultimate 耳机",
     "kind": "headset", "ids": [(0x373B, 0x1262)], "page": 0xFF90,
     "lens": {"in": 64, "out": 64}, "send": ["out"], "frames": ["55 65 01"],
     "reply": "input", "expect": [[0, 0x55], [1, 0x65]],
     "level": [2], "charge": [3, [0x03]], "full": [3, [0x04]],
     "timeout": 1200, "drain": True,
     "verified": True, "source": "Voltide（真机）"},
    {"id": "mchose20", "brand": "MCHOSE", "name": "迈从键盘", "kind": "keyboard",
     "names": {(0x3554, 0xFA09): "迈从 K99 V2 / G75 Pro"},
     "ids": [(0x3554, None)], "page": 0xFF02, "lens": {"in": 20, "out": 20},
     "send": ["setout"], "frames": ["13 4A 01 00"],
     "csum": {"kind": "sum", "from": 0, "to": 18, "at": 19},
     "reply": "input", "expect": [[0, 0x13], [1, 0x4A]],
     "rcsum": {"kind": "sum", "from": 0, "to": 18, "at": 19},
     "level": [5], "charge": [6, [0x10]], "timeout": 450, "drain": True,
     "verified": True, "source": "Voltide（K99 V2、G75 Pro 真机）· FerrisPulse"},
    {"id": "rog_omni", "brand": "ROG", "name": "ROG 无线键盘（OMNI 接收器）",
     "kind": "keyboard", "ids": [(0x0B05, 0x1ACE)], "page": 0xFF00,
     "lens": {"in": 64, "out": 64}, "lens_min": True, "send": ["out"],
     "frames": ["02 12 01"], "reply": "input",
     "expect": [[0, 0x02], [1, 0x12], [2, 0x01]], "level": [6],
     "timeout": 900, "drain": True,
     "verified": True, "source": "Voltide（AZOTH EXTREME 真机）"},
    {"id": "razer_bs", "brand": "Razer", "name": "雷蛇旋风黑鲨 V2 Pro",
     "kind": "headset", "ids": [(0x1532, 0x0555)], "page": 0xFF00,
     "lens": {"in": 64, "out": 64}, "send": ["out"],
     "frames": ["02 80 09 00 00 50 41 08 00 04 21 00 01 01"], "reply": "input",
     "expect": [[0, 0x02], [2, 0x50], [3, 0x49], [12, 0x21], [13, 0x01],
                [14, 0x01]], "level": [15], "timeout": 600, "drain": True,
     "verified": True, "source": "Voltide（真机）· FerrisPulse"},
    {"id": "darkrim", "brand": "DARKRIM", "name": "DARKRIM SHADOW 耳机",
     "kind": "headset", "ids": [(0x3654, 0x6901)], "page": 0xFF02,
     "lens": {"out": 65}, "send": ["out"], "frames": [_jl_query(0x01)],
     "reply": "input", "expect": [[1, 0xFE], [10, 0x01], [11, 0x01]],
     "level": [12], "timeout": 500, "drain": True,
     "verified": True, "source": "2.4G-battery-viewer（真机）· VigilWatt"},
    {"id": "inphic_in10", "brand": "Inphic", "name": "英菲克无线鼠标",
     "kind": "mouse",
     "ids": [(0x1D57, 0xFA60), (0x1D57, 0xFA65), (0x045E, 0x0095),
             (0x045E, 0x0090), (0x045E, 0x0093), (0x045E, 0x0099),
             (0x045E, 0x0098), (0x045E, 0x0273), (0x045E, 0x0271)],
     "page": 0x000A, "send": [], "reply": "input",
     "expect": [[0, 0x03], [2, 0x40]], "level": [4], "timeout": 2600,
     "verified": True,
     "source": "InphicBatteryTray（鼠标每 2 秒主动上报，零写入）· VigilWatt"},
    {"id": "inphic_in9", "brand": "Inphic", "name": "英菲克 IN9 无线鼠标",
     "kind": "mouse", "ids": [(0x248A, 0x5B2E), (0x248A, 0x5B2F)],
     "lens": {"feat": 33}, "lens_min": True, "send": ["feat"],
     "frames": ["0C 01 F0 00 01 01 01 FF", "0C 01 20 00 01 00 00"],
     "reply": "feature", "reply_id": 0x0C,
     "expect": [[0, 0x0C], [2, 0x20]], "nz": [6], "level": [0x12],
     "charge": [0x13, [2]], "timeout": 400,
     "verified": True, "source": "InphicBatteryTray / InphicBatteryTray-IN9（真机）"},
    {"id": "rapoo", "brand": "Rapoo", "name": "雷柏无线鼠标", "kind": "mouse",
     "ids": [(0x24AE, None)], "page": 0xFF00, "usage": 0x0002,
     "lens": {"in": 19}, "send": [], "reply": "input", "expect": [[0, 0x07]],
     "level": [8], "charge": [7, [0x02]], "timeout": 2500,
     "verified": True,
     "source": "FerrisPulse 文档 · VigilWatt（VT3 二代真机）· rapoo-tray，零写入"},
    {"id": "acer_omr", "brand": "Acer", "name": "宏碁 OMR 无线鼠标",
     "kind": "mouse",
     "ids": [(0x249A, 0x5C2F), (0x248A, 0x5D2E), (0x248A, 0x5E2E)],
     "page": 0xFF01, "usage": 0x0010, "lens": {"in": 33, "out": 33},
     "send": ["out"], "frames": ["00 10"], "reply": "input",
     "expect": [[0, 0x00], [1, 0x10], [3, 0x01]], "level": [14],
     "timeout": 600, "drain": True,
     "verified": True, "source": "FerrisPulse 文档（OMR331 贡献者真机）"},
    {"id": "am_mouse", "brand": "怒喵", "name": "怒喵 INFINITY MOUSE",
     "kind": "mouse", "ids": [(0x3151, 0x5007)], "page": 0xFFFF,
     "usage": 0x0002, "lens": {"feat": 65}, "send": ["feat"],
     "frames": ["00 F7"], "reply": "feature", "reply_id": 0x05,
     "expect": [[0, 0x05], [2, 0x00]], "oneof": [[1, [0, 1]]],
     "level": [3], "timeout": 400,
     "verified": True,
     "source": "AngryMiaoBatteryMgr（INFINITY MOUSE .100 真机）· HaloBattery · miaobat"},
    {"id": "vaxee", "brand": "VAXEE", "name": "VAXEE 无线鼠标", "kind": "mouse",
     "ids": [(0x3057, None)], "page": 0xFF05, "lens": {"feat": 64},
     "send": ["feat"], "frames": ["0E A5 0B 01 01"], "reply": "feature",
     "reply_id": 0x0E, "expect": [[0, 0x0E], [1, 0xA5], [2, 0x0B]],
     "level": [5, 5, 20], "timeout": 300,
     "verified": False, "source": "2.4G-battery-viewer · FerrisPulse 文档（两处一致）"},
    {"id": "pixart83", "brand": "PixArt", "name": "无线鼠标", "kind": "mouse",
     "names": {(0x37B0, None): "LAMZU 无线鼠标", (0x373E, None): "LAMZU 无线鼠标",
               (0x3710, None): "Pulsar 无线鼠标", (0x36A7, None): "WLmouse 无线鼠标"},
     "ids": [(0x37B0, None), (0x373E, None), (0x3710, None), (0x36A7, None)],
     "page_min": 0xFF00, "lens": {"feat": 64}, "lens_min": True,
     "send": ["feat"], "frames": ["00 00 00 02 02 00 83"], "reply": "feature",
     "reply_id": 0x00, "find": [0xA1, 0x00, 0x02, 0x02, 0x00, 0x83],
     "level": [7], "charge": [6, [1]], "timeout": 300,
     "verified": False, "source": "FerrisPulse 文档 / OpenMouse 协议"},
]


def _csum(buf: bytearray, c: dict) -> int:
    s = sum(buf[c["from"]:c["to"] + 1])
    if c["kind"] == "const":
        return (c["const"] - s) & 0xFF
    return s & 0xFF


def _spec_ids_match(spec: dict, vid: int, pid: int) -> bool:
    return any(v == vid and (p is None or p == pid) for v, p in spec["ids"])


def _spec_iface_ok(spec: dict, d) -> bool:
    if not _spec_ids_match(spec, d.vid, d.pid):
        return False
    if "page" in spec and d.usage_page != spec["page"]:
        return False
    if "page_min" in spec and d.usage_page < spec["page_min"]:
        return False
    if "usage" in spec and d.usage != spec["usage"]:
        return False
    have = {"in": d.input_len, "out": d.output_len, "feat": d.feature_len}
    for k, n in spec.get("lens", {}).items():
        if (have[k] < n) if spec.get("lens_min") else (have[k] != n):
            return False
    return True


def parse_spec_reply(spec: dict, rep: Optional[bytes]):
    """按规格解析一条回复：返回 (电量, 状态, 毫伏) 或 None。"""
    if not rep:
        return None
    base = 0
    pat = spec.get("find")
    if pat:
        n = len(pat)
        base = next((i for i in range(len(rep) - n + 1)
                     if list(rep[i:i + n]) == list(pat)), -1)
        if base < 0:
            return None
    for pos, val in spec.get("expect", []):
        if len(rep) <= base + pos or rep[base + pos] != val:
            return None
    for pos in spec.get("nz", []):
        if len(rep) <= base + pos or not rep[base + pos]:
            return None
    for pos, vals in spec.get("oneof", []):
        if len(rep) <= base + pos or rep[base + pos] not in vals:
            return None
    rc = spec.get("rcsum")
    if rc:
        if len(rep) <= rc["at"] or rep[rc["at"]] != _csum(bytearray(rep), rc):
            return None
    lv = spec["level"]
    pos, scale, vmax = lv[0], (lv[1] if len(lv) > 1 else 1), \
        (lv[2] if len(lv) > 2 else 100)
    if len(rep) <= base + pos:
        return None
    raw = rep[base + pos]
    if raw > vmax:
        return None
    level = min(100, raw * scale)
    status = "discharging"
    ch = spec.get("charge")
    if ch and len(rep) > base + ch[0] and rep[base + ch[0]] in ch[1]:
        status = "charging"
    fu = spec.get("full")
    if fu and len(rep) > base + fu[0] and rep[base + fu[0]] in fu[1]:
        status, level = "full", 100
    mv = None
    m = spec.get("mv")
    if m and len(rep) > base + m[0] + 1:
        a, b = rep[base + m[0]], rep[base + m[0] + 1]
        mv = (a << 8 | b) if m[1] == "be" else (b << 8 | a)
    return level, status, mv


def build_spec_frames(spec: dict) -> list:
    out = []
    for s in spec.get("frames", []):
        f = bytearray(bytes.fromhex(s.replace(" ", "")))
        c = spec.get("csum")
        if c:
            f += bytes(max(0, c["at"] + 1 - len(f)))
            f[c["at"]] = _csum(f, c)
        out.append(bytes(f))
    return out


def _send_frame(dev, d, frame: bytes, ways: list) -> bool:
    for way in ways:
        n = d.feature_len if way == "feat" else d.output_len
        if n < len(frame):
            continue
        buf = frame + bytes(n - len(frame))
        try:
            if way == "feat":
                ok = dev.set_feature(buf)
            elif way == "setout":
                ok = dev.set_output_report(buf)
            else:
                dev.write_output(buf)
                ok = True
        except Exception:
            ok = False
        if ok:
            return True
    return False


class Adapter:
    """一个品牌 / 一种协议。claim() 认领接口，read() 读一次电量。"""
    id = ""
    brand = ""
    name = ""
    kind = "device"
    verified = True
    source = ""

    def claim(self, ifaces: list) -> list:
        return []

    def model_name(self, ifaces: list) -> str:
        return self.name

    def read(self, ifaces: list) -> Reading:
        raise NotImplementedError

    def _ok(self, level, status="discharging", detail="", raw=b""):
        return Reading(ok=True, level=level, status=status,
                       source=f"{self.brand} 协议", detail=detail, raw=raw,
                       device_type=self.kind)

    def _asleep(self, detail="设备休眠"):
        return Reading(ok=False, status="asleep", source=f"{self.brand} 协议",
                       detail=detail, device_type=self.kind)


class SpecAdapter(Adapter):
    def __init__(self, spec: dict):
        self.spec = spec
        self.id = spec["id"]
        self.brand = spec["brand"]
        self.name = spec["name"]
        self.kind = spec.get("kind", "device")
        self.verified = spec.get("verified", False)
        self.source = spec.get("source", "")

    def claim(self, ifaces):
        return [d for d in ifaces if _spec_iface_ok(self.spec, d)]

    def model_name(self, ifaces):
        names = self.spec.get("names", {})
        for d in ifaces:
            for key in ((d.vid, d.pid), (d.vid, None)):
                if key in names:
                    return names[key]
        return self.name

    def read(self, ifaces):
        spec = self.spec
        frames = build_spec_frames(spec)
        ways = spec.get("send", [])
        tries = spec.get("tries", 1 if not ways else 3)
        timeout = spec.get("timeout", 600) / 1000.0
        for d in self.claim(ifaces):
            dev = None
            try:
                dev = HidDevice(d.path, write=bool(ways))
                if spec.get("drain") and spec.get("reply") == "input":
                    dev.drain()
                for _ in range(tries):
                    sent = all(_send_frame(dev, d, f, ways) for f in frames)
                    if ways and not sent:
                        return Reading(ok=False, source=f"{self.brand} 协议",
                                       detail="发送失败（设备被占用？）")
                    got, rep = self._wait(dev, d, timeout)
                    if got:
                        level, status, mv = got
                        detail = f"{mv} mV" if mv else ""
                        return self._ok(level, status, detail, rep)
            except Exception:
                pass
            finally:
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass
        return self._asleep()

    def _wait(self, dev, d, timeout):
        spec = self.spec
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if spec.get("reply") == "feature":
                time.sleep(0.03)
                rep = dev.get_feature_report(spec.get("reply_id", 0),
                                             d.feature_len)
            else:
                rep = dev.read_input(max(64, d.input_len), 200)
            got = parse_spec_reply(spec, rep)
            if got:
                return got, rep
        return None, b""


# 锂电池电压 → 电量（Solaar 的对照表，段内线性插值）
LOGI_MV_CURVE = ((4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
                 (3811, 50), (3778, 40), (3751, 30), (3717, 20), (3671, 10),
                 (3646, 5), (3579, 2), (3500, 0))


def logitech_mv_percent(mv: int) -> int:
    if mv >= LOGI_MV_CURVE[0][0]:
        return 100
    if mv <= LOGI_MV_CURVE[-1][0]:
        return 0
    for (vh, ph), (vl, pl) in zip(LOGI_MV_CURVE, LOGI_MV_CURVE[1:]):
        if vl <= mv <= vh:
            return round(pl + (ph - pl) * (mv - vl) / (vh - vl))
    return 0


class LogitechAdapter(Adapter):
    """罗技 LIGHTSPEED / Unifying / Bolt 接收器，HID++ 2.0。

    先 ping 1..4 号设备位找到鼠标（记住），再查 0x1000 BATTERY_STATUS（老款直接给百分比），
    没有就查 0x1004 UNIFIED_BATTERY 的功能 1 get_status，再没有就查 0x1001 BATTERY_VOLTAGE
    （G703 / G903 等只给毫伏，按 Solaar 的锂电曲线估算百分比）。0x1004 的功能 0 是能力描述，
    别读（Voltide：读错会让 G HUB 跟着显示错电量）。软件 ID 用 0xE，避开 G HUB 的 0xD。
    充电状态字节按 Solaar 的定义解析。"""
    id = "logitech"
    brand = "罗技"
    name = "罗技无线设备"
    kind = "mouse"
    source = "Voltide（G304、GPW3、M750 真机）· Solaar · FerrisPulse · VigilWatt"
    MODELS = {0xC53F: "罗技 G304", 0xC539: "罗技 G102 / G304",
              0xC547: "罗技 G PRO X SUPERLIGHT 2", 0xC54D: "罗技 G PRO X SUPERLIGHT 2",
              0xC548: "罗技 Bolt 接收器", 0xC52B: "罗技 Unifying 接收器"}
    SW = 0x0E
    _index: dict = {}

    RECEIVERS = {0xC52B, 0xC52F, 0xC531, 0xC532, 0xC534, 0xC539, 0xC53A,
                 0xC53D, 0xC53F, 0xC541, 0xC545, 0xC547, 0xC548, 0xC54D}

    def claim(self, ifaces):
        # 只认接收器：有线罗技设备也有 HID++ 集合，但设备位 1–4 永远不应答
        return [d for d in ifaces if d.vid == 0x046D and d.usage_page == 0xFF00
                and d.usage == 0x0002 and d.output_len == 20
                and (d.pid in self.RECEIVERS
                     or "receiver" in (d.product or "").lower())][:1]

    def model_name(self, ifaces):
        for d in ifaces:
            if d.pid in self.MODELS:
                return self.MODELS[d.pid]
        return self.name

    def _req(self, dev_idx, feat, fn, params=b""):
        r = bytearray(20)
        r[0], r[1], r[2] = 0x11, dev_idx, feat
        r[3] = ((fn & 0x0F) << 4) | self.SW
        r[4:4 + len(params)] = params
        return bytes(r)

    def _call(self, dev, dev_idx, feat, fn, params=b"", wait=0.6):
        req = self._req(dev_idx, feat, fn, params)
        try:
            dev.write_output(req)
        except Exception:
            return None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            p = dev.read_input(20, 60)
            if not p or len(p) < 7 or p[1] != dev_idx:
                continue
            if p[2] == 0xFF and p[3] == feat and p[4] == req[3]:
                return b""          # HID++ 错误回复：设备在，但不支持
            if p[2] == feat and p[3] == req[3]:
                return p
        return None

    def _feature(self, dev, dev_idx, fid):
        """返回 (特性号, 是否应答)；没唤醒时第一条常被吞掉，试两次。"""
        for attempt in range(2):
            p = self._call(dev, dev_idx, 0x00, 0x00,
                           bytes((fid >> 8, fid & 0xFF)),
                           0.35 if attempt == 0 else 0.5)
            if p is not None:
                return (p[4] if p else 0), True
            time.sleep(0.25)
        return 0, False

    def read(self, ifaces):
        cand = self.claim(ifaces)
        if not cand:
            return self._asleep()
        d = cand[0]
        dev = None
        try:
            dev = HidDevice(d.path, write=True)
            dev.drain()
            idx = self._index.get(d.path)
            if idx is None:
                budget = time.monotonic() + 3.6
                for i in range(1, 5):
                    left = budget - time.monotonic()
                    if left < 0.15:
                        break
                    if self._call(dev, i, 0x00, 0x01, b"\0\0\x5A",
                                  min(1.1, left)):
                        idx = self._index[d.path] = i
                        break
            if idx is None:
                return self._asleep()
            feat, answered = self._feature(dev, idx, 0x1000)
            fn, fid = 0x00, 0x1000
            if not feat:
                if not answered:
                    return self._asleep()
                feat, answered = self._feature(dev, idx, 0x1004)
                fn, fid = 0x01, 0x1004
            if not feat:
                if not answered:
                    return self._asleep()
                # G703 / G903 / G PRO Wireless 等老款只给电压（0x1001）
                feat, answered = self._feature(dev, idx, 0x1001)
                fn, fid = 0x00, 0x1001
                if not feat:
                    return (self._asleep() if not answered else
                            Reading(ok=False, source="罗技协议",
                                    detail="该设备不上报电量"))
            p = self._call(dev, idx, feat, fn)
            if not p:
                return self._asleep()
            if fid == 0x1001:
                mv, flags = p[4] << 8 | p[5], p[6]
                if not 2500 <= mv <= 5000:
                    return Reading(ok=False, source="罗技协议",
                                   detail="读数异常", raw=bytes(p))
                status = "discharging"
                if flags & 0x80:        # 充电中；低两位 1 = 已充满
                    status = "full" if flags & 0x03 == 1 else "charging"
                return self._ok(logitech_mv_percent(mv), status,
                                f"按电压估算 · {mv} mV", bytes(p))
            level, state = p[4], p[6]
            if level > 100:
                return Reading(ok=False, source="罗技协议", detail="读数异常",
                               raw=bytes(p))
            if fn == 0x00:      # 0x1000：1 充电、2 快满、3 已满、4 慢充
                status = ("full" if state == 3 else "charging"
                          if state in (1, 2, 4) else "discharging")
            else:               # 0x1004：1 充电、2 慢充、3 已满
                status = ("full" if state == 3 else "charging"
                          if state in (1, 2) else "discharging")
            return self._ok(level, status, "HID++ 2.0", bytes(p))
        except Exception:
            return self._asleep()
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass


class RazerMouseAdapter(Adapter):
    """雷蛇无线鼠标接收器：91 字节 Feature 控制通道（OpenRazer 的 razer_report）。

    命令类 0x07：0x80 电量（参数[1]，0–255 换算百分比，按 OpenRazer 一律除以 255），
    0x84 充电（参数[1] 非零）。鼠标集合拒绝读权限，句柄只申请写权限，回复用 GET_FEATURE 取。
    只认已知的无线接收器 PID，不对未知型号试探。"""
    id = "razer"
    brand = "雷蛇"
    name = "雷蛇无线鼠标"
    kind = "mouse"
    source = "Voltide（多款真机）· OpenRazer · FerrisPulse"
    PIDS = {0x007B: "Viper Ultimate", 0x00C1: "Viper V3 Pro",
            0x00DF: "Viper V3 Pro SE", 0x00B7: "DeathAdder V3 Pro",
            0x00C3: "DeathAdder V3 Pro", 0x00BF: "DeathAdder V4 Pro",
            0x00F0: "DeathAdder V4 Pro 碳纤维", 0x00E6: "Viper V4 Pro",
            0x00AB: "Basilisk V3 Pro", 0x00CD: "Basilisk V3 Pro 35K",
            0x00D7: "Basilisk V3 Pro 35K Phantom",
            0x00C5: "DeathAdder V3 HyperSpeed", 0x00D1: "Pro Click V2",
            0x00C8: "Pro Click V2 Vertical", 0x00D4: "Basilisk Mobile",
            0x00B0: "Cobra Pro", 0x00DB: "Cobra HyperSpeed",
            0x00B4: "Naga V2 HyperSpeed", 0x00B8: "Viper V3 HyperSpeed",
            0x00B9: "Basilisk V3 X HyperSpeed", 0x00B3: "HyperPolling 接收器",
            0x009A: "Pro Click Mini", 0x005A: "Lancehead",
            0x0045: "Mamba Chroma", 0xB007: "DeathAdder V4 HyperSpeed",
            0xB005: "Viper V4 HyperSpeed"}
    LEN = 91

    def claim(self, ifaces):
        return [d for d in ifaces if d.vid == 0x1532 and d.pid in self.PIDS
                and d.feature_len == self.LEN][:1]

    def model_name(self, ifaces):
        for d in ifaces:
            if d.vid == 0x1532 and d.pid in self.PIDS:
                return "雷蛇 " + self.PIDS[d.pid]
        return self.name

    @staticmethod
    def report(cls_, cmd, tx, args=b"\0\0"):
        b = bytearray(RazerMouseAdapter.LEN)
        b[2], b[6], b[7], b[8] = tx, len(args), cls_, cmd
        b[9:9 + len(args)] = args
        crc = 0
        for v in b[3:89]:
            crc ^= v
        b[89] = crc
        return bytes(b)

    def _query(self, dev, cmd, tx):
        if not dev.set_feature(self.report(0x07, cmd, tx)):
            return None, "发送失败"
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            rep = dev.get_feature_report(0, self.LEN)
            if rep and rep[2] == tx and rep[1] in (2, 3, 4, 5):
                return rep, ""
            time.sleep(0.008)
        return None, ""

    def read(self, ifaces):
        cand = self.claim(ifaces)
        if not cand:
            return self._asleep()
        dev = None
        try:
            dev = HidDevice(cand[0].path, write=True)
            for attempt in range(4):
                rep, err = self._query(dev, 0x80, 0x20 + attempt)
                if err:
                    return Reading(ok=False, source="雷蛇协议", detail=err)
                if rep is None or rep[1] in (3, 4):
                    time.sleep(0.12)
                    continue
                if rep[1] == 5:
                    return Reading(ok=False, source="雷蛇协议",
                                   detail="该型号不支持电量查询")
                if rep[1] != 2 or rep[6] < 2:
                    continue
                level = min(100, round(rep[10] * 100 / 255))
                chg, _ = self._query(dev, 0x84, 0x30 + attempt)
                charging = bool(chg and chg[1] == 2 and chg[10])
                return self._ok(level, "charging" if charging else
                                "discharging", "2.4G", rep[:14])
            return self._asleep()
        except Exception:
            return self._asleep()
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass


def user_specs() -> list:
    """用户导入的设备规格（声明式适配器，见“导入 / 导出协议”）。"""
    v = load_config().get("specs")
    out = []
    for raw in v if isinstance(v, list) else []:
        try:
            out.append(clean_spec(raw))
        except ValueError:
            pass
    return out


def adapter_knows(vid: int, pid: int) -> bool:
    """不看接口、只按 VID:PID 判断有没有适配器可能认领（拔掉的设备用）。"""
    if ((vid == 0x046D and pid in LogitechAdapter.RECEIVERS)
            or (vid == 0x1532 and pid in RazerMouseAdapter.PIDS)):
        return True
    return any(_spec_ids_match(s, vid, pid)
               for s in DEVICE_SPECS + user_specs())


def all_adapters() -> list:
    """用户导入的设备规格优先，其次内置。"""
    user = [SpecAdapter(s) for s in user_specs()]
    return user + [LogitechAdapter(), RazerMouseAdapter()] + \
        [SpecAdapter(s) for s in DEVICE_SPECS]


def adapters_for(ifaces: list) -> list:
    out = []
    for a in all_adapters():
        try:
            if a.claim(ifaces):
                out.append(a)
        except Exception:
            pass
    return out


def read_by_adapters(ifaces: list):
    """返回 (读数, 适配器)；没有适配器认领则 (None, None)。"""
    found = adapters_for(ifaces)
    asleep = None
    for a in found:
        try:
            r = a.read(ifaces)
        except Exception:
            r = None
        if r and r.ok:
            return r, a
        if asleep is None and r is not None:
            asleep = (r, a)
    return asleep if asleep else (None, None)


def protocol_source(proto: Optional[dict]) -> str:
    """卡片上显示的电量来源。"""
    proto = proto or {}
    kind = proto.get("kind", "captured")
    if kind == "standard":
        return "标准 HID 电量"
    if proto.get("source") == "import":
        return "导入协议"
    if kind == "preset":
        return "内置协议 · " + str(proto.get("name") or "厂商协议")
    return "抓包协议"

# ---------------------------------------------------------------------------
# 导入 / 导出协议：把抓包得到的协议分享给同款设备的其他用户
# ---------------------------------------------------------------------------
PROTO_FILE_FORMAT = "2.4G-protocol"


def _int_field(proto: dict, k: str, lo: int, hi: int):
    if k not in proto:
        return None
    v = proto[k]
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"字段 {k} 的值不对：{v!r}")
    return v


def clean_protocol(proto) -> dict:
    """校验一条导入的协议，只保留认识的字段；不合法抛 ValueError（中文说明）。"""
    if not isinstance(proto, dict):
        raise ValueError("协议应该是一个 JSON 对象")
    kind = proto.get("kind", "captured")
    if kind not in PROTO_KINDS:
        raise ValueError(f"不认识的协议类型：{kind}")
    if kind == "standard":
        return {"kind": "standard"}
    reqs = proto.get("requests")
    if not isinstance(reqs, list) or not reqs:
        raise ValueError("协议里没有请求帧（requests）")
    for s in reqs:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except ValueError:
            raise ValueError(f"请求帧不是十六进制：{s}") from None
        if not 2 <= len(b) <= 1024:
            raise ValueError(f"请求帧长度不对：{s}")
    kinds = proto.get("req_kinds", [])
    if not isinstance(kinds, list) or any(
            k not in ("out", "set_out", "set_feat") for k in kinds):
        raise ValueError("请求类型（req_kinds）不对")
    if proto.get("reply", "input") not in ("input", "feature", "get_input"):
        raise ValueError("回复类型（reply）不对")
    match = proto.get("match", [])
    if not isinstance(match, list) or any(
            not isinstance(m, list) or len(m) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool)
                       for x in m)
            or not (0 <= m[0] <= 1023 and 0 <= m[1] <= 255) for m in match):
        raise ValueError("回复特征（match）不对")
    for k, lo, hi in (("offset", 0, 1023), ("v", 1, 9), ("out_len", 0, 1024),
                      ("in_len", 0, 1024), ("reply_id", 0, 255),
                      ("usage_page", 0, 0xFFFF), ("tries", 1, 5),
                      ("wait_ms", 0, 2000), ("timeout_ms", 100, 3000),
                      ("scale", 1, 100), ("level_max", 1, 255)):
        _int_field(proto, k, lo, hi)
    out = {k: proto[k] for k in PROTO_KEYS if k in proto}
    out["kind"] = kind
    if "name" in out:
        out["name"] = str(out["name"])[:40]
    for k in ("invert", "strict"):
        if k in out:
            out[k] = bool(out[k])
    return out


SPEC_KINDS = ("mouse", "keyboard", "headset", "gamepad", "device")


def _hexbytes(s, what: str, lo: int = 1, hi: int = 1024) -> bytes:
    try:
        b = bytes.fromhex(str(s).replace(" ", ""))
    except ValueError:
        raise ValueError(f"{what}不是十六进制：{s}") from None
    if not lo <= len(b) <= hi:
        raise ValueError(f"{what}长度不对：{s}")
    return b


def _int(v, what: str, lo: int, hi: int) -> int:
    if isinstance(v, str):
        try:
            v = int(v, 0)
        except ValueError:
            raise ValueError(f"{what}不是数字：{v}") from None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what}的值不对：{v!r}")
    return v


def _csum_spec(c, what: str) -> dict:
    if not isinstance(c, dict) or c.get("kind") not in ("sum", "const"):
        raise ValueError(f"{what}只支持 sum / const 两种")
    out = {"kind": c["kind"], "from": _int(c.get("from"), what, 0, 1023),
           "to": _int(c.get("to"), what, 0, 1023),
           "at": _int(c.get("at"), what, 0, 1023)}
    if c["kind"] == "const":
        out["const"] = _int(c.get("const"), what, 0, 255)
    return out


def clean_spec(raw) -> dict:
    """校验一条导入的设备规格（声明式适配器）。

    参考 FerrisPulse 的 FDAS：必须带一条真机回复样本 sample，导入时用它回放解析，
    解析不出电量的规格一律拒收；会向设备写入的规格必须写明 PID，不能“同 VID 就试”。"""
    if not isinstance(raw, dict):
        raise ValueError("设备规格应该是一个 JSON 对象")
    name = str(raw.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("设备规格缺少名称（name）")
    spec = {"id": "user:" + str(raw.get("id") or name)[:40], "name": name,
            "brand": str(raw.get("brand") or name.split()[0])[:20],
            "kind": raw.get("kind", "device"), "verified": False,
            "source": "导入的设备规格"}
    if spec["kind"] not in SPEC_KINDS:
        raise ValueError(f"设备类型（kind）不对：{spec['kind']}")
    ids = raw.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 32:
        raise ValueError("缺少设备编号（ids），例如 [[\"373B\", \"1278\"]]")
    spec["ids"] = []
    for it in ids:
        if not isinstance(it, list) or len(it) != 2:
            raise ValueError(f"设备编号格式不对：{it}")
        vid = _int(int(it[0], 16) if isinstance(it[0], str) else it[0],
                   "VID", 0, 0xFFFF)
        pid = None if it[1] is None else _int(
            int(it[1], 16) if isinstance(it[1], str) else it[1], "PID", 0, 0xFFFF)
        spec["ids"].append((vid, pid))
    for k in ("page", "page_min", "usage"):
        if k in raw:
            spec[k] = _int(raw[k], k, 0, 0xFFFF)
    lens = raw.get("lens", {})
    if not isinstance(lens, dict) or any(k not in ("in", "out", "feat")
                                         for k in lens):
        raise ValueError("报告长度（lens）只认 in / out / feat")
    spec["lens"] = {k: _int(v, f"lens.{k}", 1, 1024) for k, v in lens.items()}
    spec["lens_min"] = bool(raw.get("lens_min", False))
    send = raw.get("send", [])
    if not isinstance(send, list) or any(w not in ("out", "setout", "feat")
                                         for w in send):
        raise ValueError("发送方式（send）只认 out / setout / feat")
    spec["send"] = send
    if send and any(p is None for _v, p in spec["ids"]):
        raise ValueError("会向设备写入的规格必须写明 PID")
    frames = raw.get("frames", [])
    if not isinstance(frames, list) or len(frames) > 8 or (send and not frames):
        raise ValueError("查询帧（frames）不对")
    spec["frames"] = [" ".join(f"{b:02X}" for b in _hexbytes(f, "查询帧"))
                      for f in frames]
    if raw.get("reply", "input") not in ("input", "feature"):
        raise ValueError("回复类型（reply）只认 input / feature")
    spec["reply"] = raw.get("reply", "input")
    spec["reply_id"] = _int(raw.get("reply_id", 0), "reply_id", 0, 255)
    for k in ("csum", "rcsum"):
        if k in raw:
            spec[k] = _csum_spec(raw[k], k)
    expect = raw.get("expect", [])
    if not isinstance(expect, list):
        raise ValueError("回复特征（expect）不对")
    spec["expect"] = [[_int(a, "expect", 0, 1023), _int(b, "expect", 0, 255)]
                      for a, b in (e if isinstance(e, list) and len(e) == 2
                                   else (None, None) for e in expect)]
    spec["nz"] = [_int(v, "nz", 0, 1023) for v in raw.get("nz", [])]
    spec["oneof"] = [[_int(a, "oneof", 0, 1023), [_int(x, "oneof", 0, 255)
                                                 for x in b]]
                     for a, b in (o if isinstance(o, list) and len(o) == 2
                                  and isinstance(o[1], list) else (None, [])
                                  for o in raw.get("oneof", []))]
    if "find" in raw:
        spec["find"] = list(_hexbytes(raw["find"], "特征串（find）", 2, 16))
    if not spec["expect"] and "find" not in spec:
        raise ValueError("必须给出回复特征（expect 或 find），否则无法确认回复属于这次查询")
    lv = raw.get("level")
    if not isinstance(lv, list) or not 1 <= len(lv) <= 3:
        raise ValueError("电量位置（level）应为 [位置] 或 [位置, 倍率, 原始上限]")
    spec["level"] = [_int(lv[0], "level", 0, 1023)] + \
        [_int(v, "level", 1, 255) for v in lv[1:]]
    for k in ("charge", "full"):
        if k in raw:
            v = raw[k]
            if not isinstance(v, list) or len(v) != 2 or not isinstance(v[1],
                                                                       list):
                raise ValueError(f"{k} 应为 [位置, [值…]]")
            spec[k] = [_int(v[0], k, 0, 1023),
                       [_int(x, k, 0, 255) for x in v[1]]]
    spec["timeout"] = _int(raw.get("timeout", 600), "timeout", 100, 5000)
    if "tries" in raw:
        spec["tries"] = _int(raw["tries"], "tries", 1, 5)
    spec["drain"] = bool(raw.get("drain", False))
    sample = _hexbytes(raw.get("sample", ""), "回复样本（sample）")
    if parse_spec_reply(spec, sample) is None:
        raise ValueError("回复样本（sample）按这份规格解析不出电量，请检查偏移和特征")
    spec["sample"] = " ".join(f"{b:02X}" for b in sample)
    return spec


def _spec_json(spec: dict) -> dict:
    """规格存进配置 / 导出文件时的形式（PID 为 None 原样保留）。"""
    out = {k: v for k, v in spec.items()
           if k not in ("verified", "source", "names")}
    out["id"] = str(spec["id"]).removeprefix("user:")
    out["ids"] = [[f"{v:04X}", None if p is None else f"{p:04X}"]
                  for v, p in spec["ids"]]
    return out


def parse_import(text: str, default_key: str = "") -> tuple:
    """解析导入文件，返回 ({"VID:PID": 协议}, [设备规格])。"""
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("文件内容不是有效的 JSON，可能不是协议文件") from None
    if not isinstance(data, dict):
        raise ValueError("文件格式不对，可能不是协议文件")
    specs = []
    raw_specs = data.get("specs")
    if raw_specs is not None:
        if not isinstance(raw_specs, list):
            raise ValueError("设备规格（specs）应该是一个列表")
        for i, raw in enumerate(raw_specs, 1):
            try:
                specs.append(clean_spec(raw))
            except ValueError as e:
                raise ValueError(f"第 {i} 个设备规格：{e}") from None
    protos = {}
    if specs and "protocols" not in data:
        return protos, specs
    protos = parse_protocol_import(text, default_key)
    return protos, specs


def parse_protocol_import(text: str, default_key: str = "") -> dict:
    """解析导入文件，返回 {"VID:PID": 协议}。

    认三种写法：本软件导出的文件（{"protocols": {...}}）、直接的
    {"VID:PID": 协议} 表、以及单条协议（需要 default_key 指明是哪台设备）。"""
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("文件内容不是有效的 JSON，可能不是协议文件") from None
    if not isinstance(data, dict):
        raise ValueError("文件格式不对，可能不是协议文件")
    if isinstance(data.get("protocols"), dict):
        items = data["protocols"]
    elif "requests" in data or "kind" in data:
        if not default_key:
            raise ValueError("这个文件只有一条协议，没写是哪台设备；"
                             "请在设备卡片上右键「导入协议」")
        items = {default_key: data}
    else:
        items = data
    out = {}
    for k, p in items.items():
        m = re.fullmatch(r"\s*([0-9A-Fa-f]{4})\s*:\s*([0-9A-Fa-f]{4})\s*",
                         str(k))
        if not m:
            raise ValueError(f"设备编号格式不对：{k}（应为 VID:PID，例如 373B:1216）")
        key = f"{m.group(1).upper()}:{m.group(2).upper()}"
        try:
            out[key] = clean_protocol(p)
        except ValueError as e:
            raise ValueError(f"{key}：{e}") from None
    if not out:
        raise ValueError("文件里没有协议")
    return out


def import_protocols(text: str, default_key: str = "",
                     add_devices: bool = False) -> dict:
    """导入并保存协议和设备规格；返回 {"VID:PID" 或 "spec:名称": 内容}。
    add_devices 时顺便添加协议对应的设备（CLI 用；规格对应的设备插着时会自动出现）。"""
    protos, specs = parse_import(text, default_key)
    if specs:
        cfg = load_config()
        saved = [s for s in (cfg.get("specs") or []) if isinstance(s, dict)]
        new_ids = {s["id"].removeprefix("user:") for s in specs}
        saved = [s for s in saved if str(s.get("id")) not in new_ids]
        cfg["specs"] = saved + [_spec_json(s) for s in specs]
        save_config(cfg)
    for key, p in protos.items():
        vid, pid = (int(x, 16) for x in key.split(":"))
        if p["kind"] == "standard":
            cfg = load_config()
            saved = cfg.get("protocols")
            if not isinstance(saved, dict):
                saved = {}
            saved[key] = {"kind": "standard"}
            cfg["protocols"] = saved
            save_config(cfg)
        else:
            p = protos[key] = dict(p, source="import")
            save_user_protocol(vid, pid, p, kind=p["kind"])
        forget_last(vid, pid)
    if add_devices:
        devs = load_saved_devices()
        have = {f"{d['vid']:04X}:{d['pid']:04X}" for d in devs}
        for key in protos:
            if key not in have:
                vid, pid = (int(x, 16) for x in key.split(":"))
                devs.append({"vid": vid, "pid": pid, "name": ""})
        save_saved_devices(devs)
    out = dict(protos)
    for sp in specs:
        out["spec:" + sp["name"]] = sp
    return out


def listen_targets(cand: list) -> list:
    """可以只读监听的接口：厂商自定义（usage page ≥ 0xFF00）且有输入报告。
    普通键盘 / 鼠标接口不听，按键内容不会被记录。"""
    return [d for d in cand if d.usage_page >= 0xFF00 and d.input_len > 0]


def listen_vendor_reports(cand: list, seconds: float) -> list:
    """只读监听厂商接口上设备自己发出来的报告，一个字节都不写。

    返回 [(接口标签, [(秒, 字节), …] 或 错误说明)]。每个接口一个线程同时听。"""
    targets = listen_targets(cand)
    results = [None] * len(targets)

    def work(i, d):
        frames = []
        dev = None
        try:
            dev = HidDevice(d.path, write=False)
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                rep = dev.read_input(max(64, d.input_len), 250)
                if rep:
                    frames.append((time.monotonic() - t0, bytes(rep)))
            results[i] = frames
        except Exception as e:
            results[i] = f"打不开（{e}）"
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass

    threads = [threading.Thread(target=work, args=(i, d), daemon=True)
               for i, d in enumerate(targets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(seconds + 3)
    return [(f"UP={d.usage_page:04X} in={d.input_len}",
             results[i] if results[i] is not None else "超时")
            for i, d in enumerate(targets)]


def _listen_lines(results: list, seconds: float) -> list:
    lines = [f"只读监听 {seconds:g} 秒（只听设备自己发的数据，不向设备写入任何东西）："]
    if not results:
        return lines + ["  没有可以监听的厂商接口"]
    for label, frames in results:
        if isinstance(frames, str):
            lines.append(f"  {label}：{frames}")
            continue
        lines.append(f"  {label}：共 {len(frames)} 帧")
        shown, prev, count = 0, None, 0
        for t, rep in frames + [(None, None)]:
            if rep == prev:
                count += 1
                continue
            if prev is not None and shown < 40:
                tail = f" ×{count}" if count > 1 else ""
                lines.append(f"    {t0:5.1f}s  {prev.hex(' ').upper()}{tail}")
                shown += 1
            prev, count, t0 = rep, 1, t
        if shown >= 40:
            lines.append("    ……（后面的省略）")
    return lines


def device_info_text(r: "Reading", ifaces=None, listen: float = 0) -> str:
    """设备诊断信息：接口指纹、认领它的适配器、已保存协议、最近一次读数；
    listen > 0 时再只读监听厂商接口这么多秒，附上设备自己发出的数据。"""
    vid, pid = r.vid, r.pid
    try:
        ifaces = ifaces if ifaces is not None else enumerate_hid()
    except Exception:
        ifaces = []
    cand = [d for d in ifaces if d.vid == vid and d.pid == pid]
    lines = [f"设备：{r.name}", f"VID:PID：{vid:04X}:{pid:04X}"]
    prod = next((d.product for d in cand if d.product), "")
    manu = next((d.manufacturer for d in cand if d.manufacturer), "")
    if prod or manu:
        lines.append(f"产品 / 厂商字符串：{prod or '-'} / {manu or '-'}")
    lines.append(f"HID 接口（{len(cand)} 个）：")
    lines += ["  " + d.label for d in cand] or ["  （现在没插着）"]
    found = adapters_for(cand) if cand else []
    lines.append("认领的适配器：" + ("、".join(
        f"{a.name}（{'已验证' if a.verified else '未验证'}）" for a in found)
        or "无"))
    proto = user_protocols().get(f"{vid:04X}:{pid:04X}")
    lines.append("已保存的协议：" + (proto.get("kind", "captured")
                                  if isinstance(proto, dict) else "无"))
    state = f"{r.level}%" if r.ok and r.level is not None else r.status
    lines.append(f"最近读数：{state}（{r.source or '-'}；{r.detail or '-'}）")
    if r.raw:
        lines.append("最近回复：" + bytes(r.raw[:64]).hex(" ").upper())
    if listen > 0 and cand:
        lines += _listen_lines(listen_vendor_reports(cand, listen), listen)
    lines.append(f"程序：2.4G 外设电量 · Python {sys.version.split()[0]}")
    return "\n".join(lines)


def battery_channel_possible(cand: list) -> bool:
    """这台设备有没有可能被问到电量（不看适配器，只看接口）。

    灯效设备（只有 0x0059 Lighting 接口、没有鼠标键盘接口，如主板灯效控制器、
    Windows 动态光效的虚拟设备）不算；既没有厂商自定义接口、也没有 Feature 报告的
    设备（如只有标准键鼠接口的廉价接收器）电脑无从询问，也不算。"""
    pages = {d.usage_page for d in cand}
    input_dev = any(d.usage_page == 0x0001 and d.usage in (2, 4, 5, 6)
                    for d in cand)
    if 0x0059 in pages and not input_dev:
        return False
    return any(d.usage_page >= 0xFF00 or d.feature_len > 0 for d in cand)


def worth_offering(cand: list) -> bool:
    """插拔弹窗 / 添加列表要不要列出它：能读电量，或至少可能读到。"""
    if not cand:
        return False
    vid, pid = cand[0].vid, cand[0].pid
    if adapters_for(cand) or f"{vid:04X}:{pid:04X}" in user_protocols():
        return True
    if cand[0].instance.upper().startswith(("BTHENUM", "BTHLE")):
        return False
    return battery_channel_possible(cand)


def addable_devices(ifaces: list, devices: list) -> list:
    """「添加设备」窗口的候选：插着、还没在面板里的接收器。

    能读电量的（有适配器认领或已保存协议）排在前面；其余只列出带厂商自定义接口的
    设备（2.4G 接收器基本都有），普通有线键鼠、摄像头等不会出现；蓝牙设备另有自动显示，不列。"""
    have = {(int(d["vid"]), int(d["pid"])) for d in devices}
    saved = user_protocols()
    groups: dict = {}
    for d in ifaces:
        if d.instance.upper().startswith(("BTHENUM", "BTHLE")):
            continue
        groups.setdefault((d.vid, d.pid), []).append(d)
    out = []
    for (vid, pid), cand in groups.items():
        if (vid, pid) in have:
            continue
        found = adapters_for(cand)
        supported = bool(found) or f"{vid:04X}:{pid:04X}" in saved
        if not supported and not battery_channel_possible(cand):
            continue
        prod = next((d.product for d in cand if d.product), "")
        name = (found[0].model_name(cand) if found
                else prod or f"未知设备 {vid:04X}:{pid:04X}")
        out.append({"vid": vid, "pid": pid, "name": name,
                    "supported": supported,
                    "kind": device_kind(name, "device",
                                        found[0].kind if found else "")})
    out.sort(key=lambda x: (not x["supported"], x["name"].lower()))
    return out


def export_protocols_text(keys=None) -> str:
    """导出协议为 JSON 文本；标准 HID 协议不用分享，跳过。"""
    names = {f"{d['vid']:04X}:{d['pid']:04X}": d.get("name", "")
             for d in load_saved_devices()}
    out = {}
    for key, p in user_protocols().items():
        if keys is not None and key not in keys:
            continue
        if not isinstance(p, dict) or p.get("kind") == "standard":
            continue
        q = {k: v for k, v in p.items() if k != "source"}
        if names.get(key) and not q.get("name"):
            q["name"] = names[key]
        out[key] = q
    return json.dumps({"format": PROTO_FILE_FORMAT, "version": 1,
                       "protocols": out}, ensure_ascii=False, indent=2)


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


def _reply_value(rep: Optional[bytes], off: int, shift: int, match: list,
                 vmax: int = 100):
    """回复里取电量原始值：返回 (值, 严格匹配?)；不符合就返回 None。"""
    if not rep or len(rep) <= off + shift:
        return None
    for n, (pos, val) in enumerate(match):
        if len(rep) <= pos + shift or rep[pos + shift] != val:
            if n == 0:
                return None
            v = rep[off + shift]
            return (v, False) if 0 <= v <= vmax else None
    v = rep[off + shift]
    return (v, True) if 0 <= v <= vmax else None


def _proto_reading(proto: dict, val: int, rep: bytes, how: str) -> Reading:
    val = min(100, val * int(proto.get("scale", 1) or 1))
    if proto.get("invert"):
        val = 100 - val
    return Reading(ok=True, level=val, source=protocol_source(proto),
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
    page = proto.get("usage_page")
    if page is not None:
        ifaces = [d for d in ifaces if d.usage_page == int(page)]
    tries = max(1, min(5, int(proto.get("tries", 1) or 1)))
    wait = int(proto.get("wait_ms", 0) or 0) / 1000.0
    timeout = int(proto.get("timeout_ms", 1200) or 1200) / 1000.0
    strict = bool(proto.get("strict"))
    vmax = int(proto.get("level_max", 100) or 100)
    for _ in range(tries):
        r = _replay_v2_once(ifaces, proto, reqs, off, reply, match, in_len,
                            out_len, rid, wait, timeout, strict, vmax)
        if r is not None:
            return r
    return None


def _replay_v2_once(ifaces, proto, reqs, off, reply, match, in_len, out_len,
                    rid, wait, timeout, strict, vmax):
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
                if wait:
                    time.sleep(wait)
                loose = None
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if reply == "input":
                        rep = dev.read_input(max(64, d.input_len), 250)
                    else:
                        time.sleep(0.05)
                        rep = (dev.get_feature_report(rid, d.feature_len)
                               if reply == "feature" else
                               dev.get_input_report(rid, d.input_len))
                    got = _reply_value(rep, off, shift, match, vmax)
                    if got is None:
                        continue
                    if got[1]:
                        return _proto_reading(proto, got[0], rep, "回复")
                    if loose is None and not strict:
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
    """读一遍已添加的 2.4G 设备和已连接的蓝牙设备（托盘刷新与 --once 共用），
    2.4G 设备没读到的沿用上次电量。"""
    devices = load_saved_devices() if devices is None else devices
    ifaces = enumerate_hid()
    res = {device_key(d["vid"], d["pid"]): read_saved_device(d, ifaces)
           for d in devices}
    res = apply_last_known(res)
    res.update(read_bluetooth_all())
    res.update(read_xinput_all())
    return res


def print_once():
    res = read_saved_all()
    if not res:
        print("没有设备：2.4G 设备需先在托盘程序里添加；蓝牙设备要已连接并上报电量。")
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


def cli_import(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            protos = import_protocols(f.read(), add_devices=True)
    except (OSError, ValueError) as e:
        print(f"导入失败：{e}")
        return 1
    for key, p in protos.items():
        print(f"已导入 {key}（{p.get('name') or protocol_source(p)}）")
    return 0


def cli_export(path: str) -> int:
    text = export_protocols_text()
    if not json.loads(text)["protocols"]:
        print("没有可导出的协议（标准 HID 电量不需要导出）")
        return 1
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        print(f"导出失败：{e}")
        return 1
    print(f"已导出到 {path}")
    return 0


def _arg_after(argv, flag: str) -> str:
    i = argv.index(flag)
    return argv[i + 1] if i + 1 < len(argv) else ""


def main(argv):
    if argv and argv[0] == "--capture-helper":
        _helper_run(argv[1:])
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    for flag, fn in (("--import", cli_import), ("--export", cli_export)):
        if flag in argv:
            path = _arg_after(argv, flag)
            if not path:
                print(f"用法：{flag} 文件.json")
                return 2
            return fn(path)
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
    "bluetooth": "", "hide": "",
    "import": "", "export": "",
    "edit": "", "done": "", "add": "",
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


def device_kind(name: str, fallback: str = "device",
                known: str = "") -> str:
    """设备类型：适配器知道的优先，否则按名称里的关键词猜。"""
    if known in ("mouse", "keyboard", "headset", "gamepad"):
        return known
    n = (name or "").lower()
    for words, key in ((("mouse", "鼠标", "mice"), "mouse"),
                       (("headset", "耳机", "buds", "headphone", "audio",
                         "airpods", "beats"),
                        "headset"),
                       (("keyboard", "键盘"), "keyboard"),
                       (("gamepad", "controller", "手柄"), "gamepad")):
        if any(w in n for w in words):
            return key
    return fallback


def _device_glyph(name: str, fallback: str = "device") -> str:
    return GLYPH[device_kind(name, fallback)]


# >>> DEVICE_ART（tools/make_device_art.py 生成，勿手改）
# 图标来自微软 Fluent Emoji（MIT 许可）：
# https://github.com/microsoft/fluentui-emoji
DEVICE_ART_SIZE = 88
DEVICE_ART = {
    "mouse": (
        "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"
        "55jOQQ4sxjiPWoNxhHvUnRylJt1rcbHAGSs39dDuNqrSwTl8dF+PBXuBhc8R8HQlKE1Sl0Lv"
        "FWBwJwSAakV+VJkvDftS0buZg+2H1ToIe1uXQhm1SZC1eV+47yO6jkgCPgCbR1D1iBlMM2kk"
        "KJc2VUYN+ERJpVSUOkzphsJ+Z5eAXggySflQ4TBsn7S3hdpPizvaTu6DMJiTpaCszyUx7CHL"
        "XGuSUCtoQ5KWjXte9lwC2ZBSaZV0OcqHk4w0ih9yzJo+HC/Amq5QOdNUrV4Y5BQHUjLKELbw"
        "wFBEzhFUyGxWugAZfFKoCqzO2T1/S56+Ml6Aibajqe+ViRX7ZWFKOYlHqtLht2VxqWAs7oFa"
        "PPZqQIHNIfKLLysIESC51Kk25zBagM1vfMVOx2UnTkZelAeyoKbX2QmgUeaDxSAe9CkcQQ1B"
        "X2QzBzl1dub/EtR4374Xbvuvo+7lEUuE/msH3XbbwLF8H5fUuUWMFnauSCwSjzlQ5iao1F1k"
        "uGOWhB7IrBOMR+v0fHuuu9eWiclSa9PmoK7OZ91zutO9erISXKL+lIq5o4lrKC5J+/k5PzsV"
        "m0wpTd6sw+xVefpFvh9ntkqTPYdj2ZY3m82f09evX11q8mDZyY3fPP3C3dPJ2p/W1tbsbKZV"
        "eSi9J+06mMAq05HNaoJ8mi5INyQi28Ypv9JE+ojpJhr5fsvebmfezT7zuS98+pVRpytnc/0q"
        "0eypRrWPNA3maWTE/RAF4LTbjSiynCqNPqe7jEIugsIgZ9hGiQiTIriBVzcCEgu4Kfw2V7Si"
        "DXt3njJdx6vLxmOQcZJf//z5DdW2f1xfX0+ThQrNZZPSidmuCXpAV6eKYbOYYLq3WTKUszTe"
        "X5dZnSfXt1+0M5+93HWzU19+4PPvrwTAdnnmZ8+faNrJOaMUt9vZsMU8PDWQtawTpgWSgQxk"
        "4Hd6CllQUgrYPp/xz4r6bDZ7Q3fd17764Kk3hsBh0JkTfvnTM7cZ/fvFZDI51baNSLgT1d1F"
        "ed9c/y8OgJhCEfkcljWd5SAz1ob5iS+YS/ndr3/rixeHwmDwqSme/smZTXNqjzVKPWqYfMR2"
        "OHEuHcFkqMhFLQeOdTZjwWZxR6jK77XVQN28e9t8548Q6YkHv/OVQf9QyZ7M/XHmyTNwpUXj"
        "jeG0+cpvmnO902xvNmfdcLpWw+ndfjyfypb9mZ3M7PTcjtl7z1y2v5jts6bH+xUd2tw+ffrU"
        "4Oe+538s6ukfv6B019xsTvYm8/BjBkw7Z/pBg2UbfHkDfvwSUwIZRWk8ydnunKkIGSabmbB/"
        "t8hYcrOa5983omx8LV022vzvaYv/+vb3HtCwv+wv+8v+sr/sL/vL/778B0qaa7/3lVcQAAAA"
        "AElFTkSuQmCC"
    ),
    "keyboard": (
        "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"
        "1e0Ptv0/Du7CyMNlru0AAAAASUVORK5CYII="
    ),
    "headset": (
        "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"
        "dOP4X0iDzJiQbwtBAAAAAElFTkSuQmCC"
    ),
    "gamepad": (
        "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"
        "YII="
    ),
    "device": (
        "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"
        "AAAASUVORK5CYII="
    ),
}
# <<< DEVICE_ART

ART_FOR_KIND = {"mouse": "mouse", "keyboard": "keyboard", "headset": "headset",
                "gamepad": "gamepad", "bluetooth": "headset",
                "device": "device"}


def _png_rgba(data: bytes):
    """解码内嵌的 8 位 RGBA PNG（只处理 tools/make_device_art.py 生成的格式）。"""
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", data[pos + 8:pos + 16])
        elif tag == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(4, stride):
                line[x] = (line[x] + line[x - 4]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                line[x] = (line[x] + (a + prev[x]) // 2) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                b = prev[x]
                c = prev[x - 4] if x >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc
                                      else b if pb <= pc else c)) & 255
        out += line
        prev = line
    return w, h, out


def _resize_rgba(w: int, h: int, px, nw: int, nh: int) -> bytearray:
    """面积平均缩放（预乘 alpha）；放大时退化为最近邻。"""
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = y * h / nh, (y + 1) * h / nh
        for x in range(nw):
            x0, x1 = x * w / nw, (x + 1) * w / nw
            r = g = b = a = tot = 0.0
            for sy in range(int(y0), min(h, max(int(y0) + 1,
                                                int(math.ceil(y1))))):
                wy = min(y1, sy + 1) - max(y0, sy)
                if wy <= 0:
                    continue
                for sx in range(int(x0), min(w, max(int(x0) + 1,
                                                    int(math.ceil(x1))))):
                    wx = min(x1, sx + 1) - max(x0, sx)
                    if wx <= 0:
                        continue
                    k = (sy * w + sx) * 4
                    wt_ = wx * wy
                    al = px[k + 3] / 255.0 * wt_
                    r += px[k] * al
                    g += px[k + 1] * al
                    b += px[k + 2] * al
                    a += al
                    tot += wt_
            if a > 0 and tot > 0:
                k = (y * nw + x) * 4
                out[k] = min(255, int(r / a + 0.5))
                out[k + 1] = min(255, int(g / a + 0.5))
                out[k + 2] = min(255, int(b / a + 0.5))
                out[k + 3] = min(255, int(a / tot * 255 + 0.5))
    return out


def device_art_png(kind: str, size: int) -> Optional[str]:
    """按设备类型取 3D 图标，缩放到 size 像素；没有内嵌图标时返回 None。"""
    b64 = DEVICE_ART.get(ART_FOR_KIND.get(kind, "device"))
    if not b64:
        return None
    w, h, px = _png_rgba(base64.b64decode(b64))
    return _png_b64(size, size, _resize_rgba(w, h, px, size, size))


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


def refresh_png(size: int, color, angle: float) -> str:
    """刷新图标（约 300° 圆弧 + 箭头），按 angle 度顺时针旋转。
    用图片帧代替旋转字体图标：Tk 旋转文字绕的是文字框中心而不是笔画中心，
    还会按像素对齐，转起来会晃。4×4 超采样抗锯齿。"""
    c = size / 2.0
    r = size * 0.34
    hw = size * 0.055
    th0, th1 = math.radians(75), math.radians(360)
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    # 箭头：在圆弧末端（正上方）沿顺时针切线方向
    bx, by = c + r * math.sin(th1), c - r * math.cos(th1)
    tx, ty = math.cos(th1), math.sin(th1)
    nx, ny = math.sin(th1), -math.cos(th1)
    h, w = size * 0.20, size * 0.16
    tri = ((bx + tx * h, by + ty * h), (bx + nx * w, by + ny * w),
           (bx - nx * w, by - ny * w))
    sx0, sy0 = c + r * math.sin(th0), c - r * math.cos(th0)

    def side(p, q, x, y):
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])

    def inside(x, y):
        d = math.hypot(x - c, y - c)
        if abs(d - r) <= hw:
            th = math.atan2(x - c, -(y - c)) % (2 * math.pi)
            if th0 <= th <= th1:
                return True
        if math.hypot(x - sx0, y - sy0) <= hw:
            return True
        s1 = side(tri[0], tri[1], x, y)
        s2 = side(tri[1], tri[2], x, y)
        s3 = side(tri[2], tri[0], x, y)
        return (s1 >= 0 and s2 >= 0 and s3 >= 0) or \
            (s1 <= 0 and s2 <= 0 and s3 <= 0)

    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            hit = 0
            for j in range(4):
                for i in range(4):
                    fx = x + (i + 0.5) / 4 - c
                    fy = y + (j + 0.5) / 4 - c
                    # 反向旋转采样点
                    if inside(c + fx * ca + fy * sa, c - fx * sa + fy * ca):
                        hit += 1
            if hit:
                k = (y * size + x) * 4
                px[k], px[k + 1], px[k + 2] = color
                px[k + 3] = int(hit / 16 * 255 + 0.5)
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

    def _spec_art(self, kind, size):
        D = self._px(size)
        return (("art", kind, D), lambda: device_art_png(kind, D) or
                blank_png(D, D))

    def _art(self, kind, size):
        """设备的 3D 图标（微软 Fluent Emoji）；没有内嵌图标时返回 None。"""
        if not DEVICE_ART.get(ART_FOR_KIND.get(kind, "device")):
            return None
        return self._img(self._spec_art(kind, size))

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
        elif kind == "danger":
            fills = [c["ctl"], _mix(c["ctl_hover"], c["bad"], 0.16)]
            stroke, col, hcol = c["ctl_stroke"], c["bad"], c["bad"]
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
        self._ctext(32, top + 44, "还没适配：右键卡片「复制设备信息」提 Issue，或导入协议文件。",
                    c["sub"], self._f(10), anchor="w")
        self._link(W - 32, top + 22, "有协议文件？直接导入", self._import,
                   "lnk_import", size=9, anchor="e")
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

    def _import(self):
        if self.parent.import_protocol(self.key, owner=self.root):
            self.close(animate=True)

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

    def __init__(self, app, vid: int, pid: int, name: str, level=None,
                 kind: str = ""):
        self.app = app
        self.kind = device_kind(name, "device", kind)
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
        art = self._art(self.kind, 40)
        if art is not None:
            cv.create_image(37, 80, anchor="nw", image=art)
        else:
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


class AddPickerWindow(_Fluent):
    """「添加设备」：列出插着、还没在面板里的接收器，点一下就添加，不用插拔。"""

    LW = 420
    ROW = 64
    MAX_ROWS = 6

    def __init__(self, panel):
        self.panel = panel
        self.app = panel.app
        self._closed = False
        self._added = set()
        self.items = self._scan()
        rows = max(1, min(len(self.items), self.MAX_ROWS))
        self.LH = 96 + rows * self.ROW + (rows - 1) * 6 + 24 + 72
        self._new_window(panel.root, "添加设备", self.app.theme_light,
                         self.LW, self.LH)
        self.cv.bind("<Button-1>", lambda e: self._drag_press(
            e, 52, ("btn_",)))
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>",
                     lambda e: setattr(self, "_drag", None))
        self.root.bind("<Escape>", lambda e: self.close())
        x, y = self._center_on_work_area()
        self.root.update_idletasks()
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

    def _scan(self) -> list:
        try:
            ifaces = enumerate_hid()
        except Exception:
            ifaces = []
        return addable_devices(ifaces, self.app.devices)

    def render(self):
        c = self.c
        cv = self.cv
        cv.delete("all")
        self._btns = {}
        W, H = self.LW, self.LH
        fy = H - 72
        cv.create_image(0, 0, anchor="nw", image=self._grad(W, 0, fy, fy))
        self._ctext(24, 32, "添加设备", c["text"], self._f(15, True),
                    anchor="w")
        self._ctext(24, 58, "这台电脑上插着、还没在面板里的接收器", c["sub"],
                    self._f(10), anchor="w")
        self._button(W - 46, 0, 46, 32, GLYPH["close"], self.close,
                     kind="close", tag="btn_close", glyph=True, size=10)
        y = 84
        if not self.items:
            cv.create_image(24, y, anchor="nw", image=self._rr(
                W - 48, self.ROW, 6, c["card"], c["stroke_top"],
                c["stroke_bot"]))
            self._ctext(W / 2, y + 22, "没有找到可以添加的设备", c["text"],
                        self._f(11, True))
            self._ctext(W / 2, y + 44, "插上 2.4G 接收器后，点下面的「重新查找」",
                        c["sub"], self._f(9))
        for i, it in enumerate(self.items[:self.MAX_ROWS]):
            self._row(24, y, W - 48, it, i)
            y += self.ROW + 6
        more = len(self.items) - self.MAX_ROWS
        if more > 0:
            self._ctext(24, fy - 14, f"还有 {more} 个设备没列出，先添加上面的再重新查找",
                        c["dim"], self._f(9), anchor="w")
        cv.create_rectangle(0, fy, W, H, fill=_rgb(c["footer"]), outline="")
        cv.create_line(0, fy, W, fy, fill=_rgb(c["divider"]),
                       width=self._px(1))
        bw = (W - 48 - 8) / 2
        self._button(24, fy + 20, bw, 32, "重新查找", self.rescan,
                     tag="btn_rescan")
        self._button(24 + bw + 8, fy + 20, bw, 32, "完成", self.close,
                     kind="accent", tag="btn_done")
        cv.scale("all", 0, 0, self.S, self.S)

    def _row(self, x, y, w, it, i):
        c = self.c
        cv = self.cv
        cv.create_image(x, y, anchor="nw", image=self._rr(
            w, self.ROW, 6, c["card"], c["stroke_top"], c["stroke_bot"]))
        cy = y + self.ROW / 2
        ok = it["supported"]
        art = self._art(it.get("kind", "device"), 38)
        if art is not None:
            cv.create_image(x + 11, cy - 19, anchor="nw", image=art)
        else:
            well = _mix(c["card"], c["ok"] if ok else c["dim"], 0.16)
            cv.create_image(x + 12, cy - 18, anchor="nw",
                            image=self._disc(36, well))
            self._ctext(x + 30, cy, _device_glyph(it["name"]), c["text"],
                        self._fi(15))
        key = (it["vid"], it["pid"])
        added = key in self._added
        bw = 72
        self._ctext(x + 60, cy - 10, self._fit(it["name"], self._f(11, True),
                                               w - 60 - bw - 24),
                    c["text"], self._f(11, True), anchor="w")
        sub = ("能读取电量" if ok else "暂不支持读取电量") + \
            f" · {it['vid']:04X}:{it['pid']:04X}"
        self._ctext(x + 60, cy + 11, sub, c["ok"] if ok else c["sub"],
                    self._f(9), anchor="w")
        self._button(x + w - 12 - bw, cy - 15, bw, 30,
                     "已添加" if added else "添加",
                     lambda it=it: self.add(it),
                     kind="accent" if ok and not added else "standard",
                     enabled=not added, tag=f"btn_add_{i}", size=10)

    def add(self, it):
        try:
            self.app.add_device(it["vid"], it["pid"], it["name"])
        except Exception:
            pass
        self._added.add((it["vid"], it["pid"]))
        self.render()

    def rescan(self):
        self.items = self._scan()
        self._added = set()
        rows = max(1, min(len(self.items), self.MAX_ROWS))
        lh = 96 + rows * self.ROW + (rows - 1) * 6 + 24 + 72
        if lh != self.LH:
            self.LH = lh
            self.H = self._px(lh)
            self.cv.configure(height=self.H)
            self.root.geometry(f"{self.W}x{self.H}")
            apply_fluent_frame(self.root, self.W, self.H, self._px(8),
                               self.c["stroke"])
        self.render()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.panel._picker = None
        if self._added and not self.app.panel_only:
            try:
                self.panel.show()
            except Exception:
                pass

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
    TEMP_H = 44
    FOOT_H = 52
    CARD_H = 68
    GAP = 6
    EMPTY_H = 210
    RING = 44

    def __init__(self, app: "TrayApp"):
        self.app = app
        self.root = tk.Tk()
        self.root.title("2.4G")
        self.root.overrideredirect(True)
        self._fl_init(app.theme_light)
        self.W = self._px(self.LW)
        self.LH = self.HEAD_H + self.TEMP_H + self.EMPTY_H + 12 + self.FOOT_H
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
        self._editing = False
        self._picker = None
        self._listening = False
        self._note = ""
        self._scroll = 0.0
        self._render_scroll = 0.0
        self._max_scroll = 0.0
        self._list = (self.HEAD_H + self.TEMP_H,) * 2
        self._temp_items = []
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
        self._spin_item = None
        self._spin_frame = -1
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
        specs += [self._spec_refresh(f) for f in range(36)]
        specs += [self._spec_art(k, 40) for k in ("mouse", "keyboard",
                                                   "headset", "gamepad",
                                                   "device")]
        todo = [s for s in specs if s[0] not in _B64]

        def work():
            for key, make in todo:
                try:
                    _B64.setdefault(key, make())
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _fit_rows(self) -> int:
        """面板有几台设备就显示几行，不用滚动；只有屏幕放不下时才限制行数。"""
        try:
            _l, t, _r, b, _sw, _sh = work_area()
        except Exception:
            t = b = 0
        if b - t <= 0:
            return 50
        avail = (b - t) / self.S - 2 * 12 - (self.HEAD_H + self.TEMP_H + 12
                                              + self.FOOT_H)
        return max(1, int((avail + self.GAP) // (self.CARD_H + self.GAP)))

    def _spec_refresh(self, frame):
        D, col = self._px(16), self.c["sub"]
        return (("refresh", D, col, frame),
                lambda: refresh_png(D, col, frame * 10))

    def _refresh_img(self, frame):
        return self._img(self._spec_refresh(frame))

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
        self._editing = False
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
        if self._picker is not None:
            try:
                self._picker.root.destroy()
            except Exception:
                pass
            self._picker = None
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
            vis = min(n, self._fit_rows())
            list_h = vis * self.CARD_H + (vis - 1) * self.GAP
            content = n * self.CARD_H + (n - 1) * self.GAP
        else:
            list_h = content = self.EMPTY_H
        self._max_scroll = max(0.0, float(content - list_h))
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))
        top = self.HEAD_H + self.TEMP_H
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
        self._temp_row(self.HEAD_H - 2)
        bx = W - 12 - 32
        if self.app.panel_only:
            self._button(bx, 13, 32, 32, GLYPH["close"], self.hide,
                         kind="subtle", tag="btn_close", glyph=True, size=11)
            bx -= 36
        self._button(bx, 13, 32, 32, "", self._refresh,
                     kind="subtle", tag="btn_refresh", glyph=True, size=13)
        self._spin_item = cv.create_image(
            bx + 16, 29, tags=("btn_refresh",),
            image=self._refresh_img(int(round(self._spin_angle / 10)) % 36))
        self._spin_frame = -1
        if n or self._editing:
            bx -= 36
            self._button(bx, 13, 32, 32,
                         GLYPH["done"] if self._editing else GLYPH["edit"],
                         self._toggle_edit, kind="subtle", tag="btn_edit",
                         glyph=True, size=13)
        if n and not self._editing:
            bx -= 36
            self._button(bx, 13, 32, 32, GLYPH["add"], self.open_picker,
                         kind="subtle", tag="btn_add_device", glyph=True,
                         size=13)

        # 底栏
        cv.create_rectangle(0, foot_y, W, LH, fill=_rgb(c["footer"]),
                            outline="")
        cv.create_line(0, foot_y, W, foot_y, fill=_rgb(c["divider"]),
                       width=self._px(1))
        self._ctext(18, foot_y + self.FOOT_H / 2,
                    self._note or ("编辑中：可以改名或删除设备" if self._editing
                                   else self._updated_text()),
                    c["link"] if (self._editing or self._note) else c["sub"],
                    self._f(10),
                    anchor="w")
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

    # -- CPU / GPU 温度 -------------------------------------------------------
    def _temp_chips(self):
        t = getattr(self.app, "_temps", None) or {}
        chips = [("CPU", t.get("cpu"))]
        gpus = (t.get("gpus") or [])[:2]
        if gpus:
            labels = [gpu_label(g.get("name", ""), f"GPU{i + 1}")
                      for i, g in enumerate(gpus)]
            if len(gpus) == 1 or len(set(labels)) < len(labels):
                labels = (["GPU"] if len(gpus) == 1 else
                          [f"GPU{i + 1}" for i in range(len(gpus))])
            for label, g in zip(labels, gpus):
                chips.append((label, g))
        else:
            chips.append(("GPU", None))
        return chips, bool(t)

    @staticmethod
    def _temp_text(v, loaded: bool) -> str:
        if not v:
            return "—" if loaded else "读取中"
        return ("≈" if v.get("approx") else "") + f"{v['value']:.0f}°C"

    def _temp_row(self, y):
        """标题下方一排温度小卡：圆点颜色随温度（<70 绿、<85 黄、更高红）。"""
        c = self.c
        cv = self.cv
        chips, loaded = self._temp_chips()
        n = len(chips)
        gap = 8
        cw = (self.LW - 24 - gap * (n - 1)) / n
        self._temp_items = []
        for i, (label, v) in enumerate(chips):
            x = 12 + i * (cw + gap)
            value = v["value"] if v else None
            cv.create_image(x, y, anchor="nw", image=self._rr(
                cw, 34, 6, c["card"], c["stroke_top"], c["stroke_bot"]))
            dot = cv.create_image(x + 12, y + 13, anchor="nw",
                                  image=self._disc(8, temp_rgb(value, c)))
            self._ctext(x + 26, y + 17, label, c["sub"], self._f(9, True),
                        anchor="w")
            val = self._ctext(x + cw - 12, y + 17, self._temp_text(v, loaded),
                              c["text"] if v else c["dim"], self._fn(11),
                              anchor="e")
            self._temp_items.append((label, dot, val))

    def update_temps(self):
        """温度刷新只改数字和圆点，不整面板重绘（不打断动效、悬停）。"""
        if not self._visible or self._entry:
            return
        chips, loaded = self._temp_chips()
        if [l for l, _v in chips] != [l for l, _d, _t in self._temp_items]:
            self.refresh_view()
            return
        for (_l, v), (_l2, dot, val) in zip(chips, self._temp_items):
            value = v["value"] if v else None
            try:
                self.cv.itemconfigure(dot, image=self._disc(
                    8, temp_rgb(value, self.c)))
                self.cv.itemconfigure(
                    val, text=self._temp_text(v, loaded),
                    fill=_rgb(self.c["text"] if v else self.c["dim"]))
            except tk.TclError:
                pass

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
        if r.kind == "bluetooth":
            st = "电量低" if r.level is not None and r.level < 20 else (
                r.detail or "")
            return ("蓝牙 · " + st if st else "蓝牙"), False
        if r.kind == "gamepad":
            return "手柄 · " + (r.detail or "XInput"), False
        if r.status == "unknown_proto":
            return "协议未知", True
        if r.status == "no_battery":
            return "不提供电量信息", False
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

        # 设备图标：3D 图标（微软 Fluent Emoji），类型优先按适配器认定的
        kind = device_kind(r.name, {"bluetooth": "bluetooth",
                                    "gamepad": "gamepad"}.get(r.kind, "device"),
                           r.device_type)
        art = self._art(kind, 40)
        if art is not None:
            cv.create_image(x1 + 13, cy - 20, anchor="nw", tags=tags,
                            image=art)
        else:
            well = _mix(c["card"], col, 0.16) if lv is not None else c["well"]
            cv.create_image(x1 + 14, cy - 19, anchor="nw", tags=tags,
                            image=self._disc(38, well))
            gcol = _mix(col, c["text"], 0.35) if lv is not None else c["text"]
            texts.append((self._ctext(x1 + 33, cy, GLYPH[kind if kind in GLYPH
                                                         else "device"],
                                      gcol, self._fi(15), tags=tags), gcol))
        if r.ok and r.status == "charging" and not r.stale:
            b = cv.create_image(x1 + 38, cy + 5, anchor="nw", tags=tags,
                                image=self._disc(16, c["accent"]))
            self._badges.append(b)
            self._ctext(x1 + 46, cy + 13, GLYPH["bolt"], c["on_accent"],
                        self._fi(8), tags=tags)

        tx = x1 + 64
        rx = x2 - 14 - (122 if self._editing else self.RING)
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

        if self._editing:
            # 编辑模式：圆环换成删除 / 隐藏按钮
            hide = r.kind in ("bluetooth", "gamepad")
            for bx_, bw_, text, cmd, kind_, btag in (
                    (x2 - 14 - 122, 56, "改名",
                     lambda k=key: self._edit_name_at(k), "standard",
                     f"btn_ren_{key}"),
                    (x2 - 14 - 60, 60, "隐藏" if hide else "删除",
                     lambda k=key: self.confirm_delete(k), "danger",
                     f"btn_del_{key}")):
                btn = self._button(bx_, cy - 15, bw_, 30, text, cmd,
                                   kind=kind_, tag=btag, size=10)
                cv.addtag_withtag("lst", btn)
                cv.addtag_withtag(tag, btn)
            self._card_texts[key] = texts
            cv.tag_bind(tag, "<Enter>",
                        lambda e, k=key: self._hover_card(k, True))
            cv.tag_bind(tag, "<Leave>",
                        lambda e, k=key: self._hover_card(k, False))
            return

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
        cv.create_image(cx - 28, cy - 88, anchor="nw",
                        image=self._disc(56, _mix(c["card"], c["accent"],
                                                  0.14)))
        icon = self._ctext(cx, cy - 60, GLYPH["device"], c["link"],
                           self._fi(24))
        self._bob = (icon, cy - 60)
        self._ctext(cx, cy - 14, "还没有设备", c["text"], self._f(12, True))
        self._ctext(cx, cy + 10, "支持的设备插上会自动出现", c["sub"],
                    self._f(10))
        self._button(cx - 64, cy + 30, 128, 32, "添加设备", self.open_picker,
                     kind="accent", tag="btn_empty_add", size=10)
        self._link(cx, cy + 82, "有别人分享的协议文件？点这里导入",
                   self.import_protocol, "btn_empty_import", size=9,
                   anchor="center")

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
        spinning = self.app._refreshing or now < self._spin_until
        if spinning:
            self._spin_angle = (self._spin_angle + 10) % 360
        elif self._spin_angle:
            self._spin_angle = self._spin_angle + 10
            if self._spin_angle >= 360:
                self._spin_angle = 0.0
        frame = int(round(self._spin_angle / 10)) % 36
        if self._spin_item and frame != self._spin_frame:
            self._spin_frame = frame
            try:
                cv.itemconfigure(self._spin_item,
                                 image=self._refresh_img(frame))
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
        if r is not None and r.kind == "hid":
            items.append((GLYPH["import"], "导入协议…",
                          lambda k=key: self.import_protocol(k)))
            items.append((GLYPH["info"], "复制设备信息",
                          lambda k=key: self.copy_device_info(k)))
            proto = user_protocols().get(f"{r.vid:04X}:{r.pid:04X}")
            if isinstance(proto, dict) and proto.get("kind") != "standard":
                items.append((GLYPH["export"], "导出协议…",
                              lambda k=key: self.export_protocol(k)))
        items.append((GLYPH["refresh"], "立即刷新", self._refresh))
        items.append(None)
        if r is not None and r.kind in ("bluetooth", "gamepad"):
            items.append((GLYPH["hide"], "不再显示",
                          lambda k=key: self.delete_device(k)))
        else:
            items.append((GLYPH["delete"], "删除设备",
                          lambda k=key: self.confirm_delete(k)))
        self._menu = FluentMenu(self, items, e.x_root, e.y_root)

    # -- 导入 / 导出协议 ------------------------------------------------------
    def _device_id(self, key) -> str:
        r = self.app.snapshot().get(key) if key else None
        return f"{r.vid:04X}:{r.pid:04X}" if r is not None and r.vid else ""

    def import_protocol(self, key=None, owner=None) -> bool:
        """选一个协议文件导入；成功后自动添加设备并刷新。"""
        self._close_menu()
        owner = owner or self.root
        self._modal = True
        try:
            path = filedialog.askopenfilename(
                parent=owner, title="导入协议文件",
                filetypes=[("协议文件", "*.json"), ("所有文件", "*.*")])
            if not path:
                return False
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    protos = import_protocols(f.read(), self._device_id(key))
            except (OSError, ValueError) as e:
                messagebox.showerror("导入协议", f"没能导入：{e}",
                                     parent=owner)
                return False
            for k, p in protos.items():
                if k.startswith("spec:"):
                    continue
                vid, pid = (int(x, 16) for x in k.split(":"))
                self.app.add_device(vid, pid, "")
            self.app.auto_add_known()
            names = "、".join(
                f"{p.get('name') or k}" for k, p in list(protos.items())[:3])
            more = f" 等 {len(protos)} 台设备" if len(protos) > 3 else ""
            messagebox.showinfo(
                "导入协议", f"已导入：{names}{more}\n\n设备插着就会马上读取电量；"
                "没插的话，插上接收器就能看到。", parent=owner)
            self.app.refresh()
            return True
        finally:
            self._modal = False

    LISTEN_SECONDS = 10

    def copy_device_info(self, key):
        """把这台设备的接口指纹和最近一次回复复制到剪贴板，提 Issue 适配新设备用。
        有厂商接口时可以先只读监听 10 秒，把设备自己发出的数据也附上。"""
        self._close_menu()
        r = self.app.snapshot().get(key)
        if r is None or not r.vid or self._listening:
            return
        try:
            cand = [d for d in enumerate_hid()
                    if d.vid == r.vid and d.pid == r.pid]
        except Exception:
            cand = []
        listen = 0
        if listen_targets(cand):
            self._modal = True
            try:
                ok = messagebox.askyesnocancel(
                    "复制设备信息",
                    f"要不要顺便监听 {self.LISTEN_SECONDS} 秒？\n\n"
                    "监听时只读取设备自己发出的数据，不会向设备写入任何东西，"
                    "也不记录普通按键。\n\n"
                    f"点「是」后的 {self.LISTEN_SECONDS} 秒里，请把设备关机再开机"
                    "（很多设备会在连上时报告电量），也可以正常用一用。\n\n"
                    "「是」= 监听后复制　「否」= 只复制接口信息",
                    parent=self.root)
            finally:
                self._modal = False
            if ok is None:
                return
            listen = self.LISTEN_SECONDS if ok else 0
        if not listen:
            self._finish_copy(device_info_text(r, cand), False)
            return
        done = queue.SimpleQueue()
        self._listening = True
        self._note = f"正在监听设备数据（{listen} 秒），请把设备关机再开机…"
        self.render()
        threading.Thread(target=lambda: done.put(
            device_info_text(r, cand, listen)), daemon=True).start()

        def poll():
            if done.empty():
                self.root.after(200, poll)
                return
            self._listening = False
            self._note = ""
            self.render()
            self._finish_copy(done.get(), True)
        self.root.after(200, poll)

    def _finish_copy(self, text: str, listened: bool):
        self._modal = True
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo(
                "复制设备信息", "已复制到剪贴板" +
                ("（含监听到的数据）" if listened else "") +
                "。\n\n想让软件支持这台设备，可以到项目主页提一个 Issue，"
                "把这段信息粘贴进去，并写上设备型号。", parent=self.root)
        finally:
            self._modal = False

    def export_protocol(self, key):
        self._close_menu()
        dev_id = self._device_id(key)
        if not dev_id:
            return
        r = self.app.snapshot().get(key)
        base = re.sub(r'[\\/:*?"<>|\s]+', "_", (r.name or "").strip()) or "设备"
        self._modal = True
        try:
            path = filedialog.asksaveasfilename(
                parent=self.root, title="导出协议文件",
                defaultextension=".json",
                initialfile=f"{base}_{dev_id.replace(':', '')}.json",
                filetypes=[("协议文件", "*.json")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(export_protocols_text([dev_id]))
            except OSError as e:
                messagebox.showerror("导出协议", f"没能保存：{e}",
                                     parent=self.root)
                return
            messagebox.showinfo(
                "导出协议", f"已保存到：\n{path}\n\n把这个文件发给用同款设备的朋友，"
                "对方右键托盘图标 →「导入协议…」选中它就能用。",
                parent=self.root)
        finally:
            self._modal = False

    def open_picker(self):
        """「添加设备」窗口；打开时面板先收起，关掉后再弹出来看到新设备。"""
        self._close_menu()
        old = self._picker
        if old is not None:
            try:
                old.root.lift()
                old.root.focus_force()
            except Exception:
                pass
            return
        self._picker = AddPickerWindow(self)
        if not self.app.panel_only:
            self._withdraw()

    def _toggle_edit(self):
        self._editing = not self._editing and bool(self.app.snapshot())
        self.render()

    def confirm_delete(self, key: str):
        """删除前确认，并告诉用户之后怎么加回来。"""
        self._close_menu()
        r = self.app.snapshot().get(key)
        if r is None:
            return
        name = r.name or "这台设备"
        if r.kind in ("bluetooth", "gamepad"):
            title, msg = "隐藏设备", (
                f"不再显示「{name}」？\n\n想恢复的话，在托盘右键菜单里把"
                "「显示蓝牙设备电量」关掉再打开。")
        else:
            title, msg = "删除设备", (
                f"从面板里删除「{name}」？\n\n之后插上也不会再自动添加；想加回来，"
                "点面板右上角的「+」，在列表里选它。")
        self._modal = True
        try:
            ok = messagebox.askyesno(title, msg, parent=self.root)
        finally:
            self._modal = False
        if ok:
            self.delete_device(key)
            if not self.app.snapshot():
                self._editing = False
                self.render()

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
TRAY_OPEN, TRAY_REFRESH, TRAY_STARTUP, TRAY_QUIT, TRAY_BT = 1, 2, 3, 4, 5
TRAY_IMPORT = 6
TRAY_LOW = 7


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


def tray_summary(snap: dict) -> tuple:
    """托盘图标的内容：(最低电量, 最低那台是否在充电, 悬停提示)。
    提示第一行是最低电量的设备，下面按电量从低到高列出其余设备（系统限 127 字）。"""
    rs = sorted((r for r in snap.values()
                 if r and r.present and r.ok and r.level is not None),
                key=lambda r: r.level)
    if not rs:
        return None, False, ("2.4G · 没有设备" if not snap
                             else "2.4G · 暂无电量读数")
    low = rs[0]
    lines = [f"2.4G · 最低 {low.level}%：{low.name or '设备'}"]
    lines += [f"{r.name or '设备'}  {r.level}%" for r in rs[1:]]
    tip = lines[0]
    for line in lines[1:]:
        if len(tip) + 1 + len(line) > 127:
            break
        tip += "\n" + line
    return low.level, low.status == "charging", tip[:127]


LOW_ALERT_AT = 20       # 和面板上“电量低”的标准一致
LOW_ALERT_AGAIN = 10
LOW_ALERT_RESET = 30


def low_alerts(res: dict, state: dict) -> list:
    """返回这一轮需要提醒的设备（电量从低到高）；state 记着每台已经提醒到第几档。"""
    out = []
    for key, r in res.items():
        if not r or not r.ok or r.level is None or r.stale or not r.present:
            continue
        if r.status in ("charging", "full", "wired") or \
                r.level >= LOW_ALERT_RESET:
            state.pop(key, None)
            continue
        stage = (2 if r.level <= LOW_ALERT_AGAIN
                 else 1 if r.level < LOW_ALERT_AT else 0)
        if stage > state.get(key, 0):
            state[key] = stage
            out.append(r)
    return sorted(out, key=lambda r: r.level)


def low_alert_text(rs: list) -> tuple:
    """(正文, 标题)"""
    if len(rs) == 1:
        r = rs[0]
        name = r.name or "设备"
        if r.level <= LOW_ALERT_AGAIN:
            return f"{name} 只剩 {r.level}% 电量，尽快充电吧", "电量很低"
        return f"{name} 还剩 {r.level}% 电量，记得充电", "电量低"
    names = "、".join(f"{r.name or '设备'} {r.level}%" for r in rs)
    return f"这些设备该充电了：{names}", "电量低"


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
        self._big_icons: dict[int, int] = {}

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
        """托盘始终只有一个图标：显示所有设备里最低的电量。"""
        nid = self.nids.get(1)
        if nid is None:
            nid = self._make_nid(1)
            self._fill_icon(nid)
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self.nids[1] = nid
        else:
            self._fill_icon(nid)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _fill_icon(self, nid: NOTIFYICONDATAW):
        lvl, charging, tip = tray_summary(self.app.snapshot())
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
        nid.szTip = tip

    def refresh_icons(self):
        self.sync_icons()

    def notify(self, text: str, title: str = "2.4G",
               level: int = 100) -> bool:
        """托盘气泡通知（Windows 通知中心也会收到），配 64px 电量圆环图标。"""
        nid = self.nids.get(1)
        if nid is None:
            return False
        icon = self._big_icons.get(level)
        if not icon:
            icon = make_battery_icon(level, self.app.theme_light, False,
                                     size=64)
            if icon:
                self._big_icons[level] = icon
        old_icon = nid.hIcon
        if icon:
            nid.hIcon = icon
        nid.uFlags = NIF_INFO | NIF_ICON
        nid.uVersion = 10000          # 10 秒
        nid.szInfo = text[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        nid.hBalloonIcon = icon or 0
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
        for h in self._big_icons.values():
            user32.DestroyIcon(h)
        self._big_icons.clear()

    def _context_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            # 每台设备一行（参考 Voltide），点了打开面板
            rows = sorted(self.app.snapshot().values(),
                          key=lambda r: (r.level is None, r.level or 0))
            for r in rows[:8]:
                lv = f"{r.level}%" if r.ok and r.level is not None else "--"
                mark = " ⚡" if r.ok and r.status == "charging" else ""
                name = (r.name or "设备")[:24].replace("&", "&&")
                user32.AppendMenuW(menu, MF_STRING, TRAY_OPEN,
                                   f"{name}\t{lv}{mark}")
            if rows:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, TRAY_OPEN, "打开电量面板")
            user32.AppendMenuW(menu, MF_STRING, TRAY_REFRESH, "立即刷新")
            user32.AppendMenuW(menu, MF_STRING, TRAY_IMPORT, "导入协议…")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if
                               load_config().get("bluetooth", True) else 0),
                               TRAY_BT, "显示蓝牙设备电量")
            user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if
                               load_config().get("low_alert", True) else 0),
                               TRAY_LOW, "低电量提醒")
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
        self._low: dict[str, int] = {}
        self.devices = load_saved_devices()
        self._seen = None
        self._asked = set()
        self._pending_add = None
        self._add_win = None
        self._last_poll = 0.0
        self._hotplug_requested = False
        self._toggle_requested = False
        self._temps: dict = {}
        self._temps_at = 0.0
        self._temps_busy = False
        self._temps_new = False
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
        self._check_low(res)
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

    def _check_low(self, res):
        """电量掉到 20% 以下弹一次气泡，到 10% 以下再提醒一次；
        充上电或回到 30% 以上重新布防。同一轮有几台一起低就合成一条。"""
        if not load_config().get("low_alert", True):
            return
        new = low_alerts(res, self._low)
        if not new or not self.tray:
            return
        try:
            self.tray.notify(*low_alert_text(new), level=new[0].level)
        except Exception:
            pass

    # -- 交互 --------------------------------------------------------------
    def request_show(self):
        self._show_requested = True

    def _poll_temps(self):
        """面板打开时每 3 秒在后台线程读一次 CPU / GPU 温度。"""
        if (self._temps_busy or not self.panel.visible
                or time.time() - self._temps_at < 3):
            return
        self._temps_busy = True

        def work():
            try:
                self._temps = read_temperatures()
            except Exception:
                self._temps = {"cpu": None, "gpus": [], "ts": time.time()}
            self._temps_at = time.time()
            self._temps_new = True
            self._temps_busy = False
        threading.Thread(target=work, daemon=True).start()

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
        elif cmd == TRAY_IMPORT:
            self.panel.import_protocol()
        elif cmd == TRAY_STARTUP:
            self.panel._toggle_startup()
        elif cmd == TRAY_BT:
            cfg = load_config()
            on = not cfg.get("bluetooth", True)
            cfg["bluetooth"] = on
            if on:
                cfg["bt_hidden"] = []  # 重新打开时把单独隐藏的也恢复
            save_config(cfg)
            self.refresh()
        elif cmd == TRAY_LOW:
            cfg = load_config()
            cfg["low_alert"] = not cfg.get("low_alert", True)
            save_config(cfg)
            self._low.clear()
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
            ifaces = enumerate_hid()
        except Exception:
            return
        present = {(d.vid, d.pid) for d in ifaces}
        if self._seen is None:
            self._seen = present
            self._asked = set(present)
            self._auto_add(ifaces, present)
            return
        new = present - self._seen
        self._seen = present
        self._asked &= present
        self._auto_add(ifaces, new)
        for vid, pid in sorted(new):
            if (vid, pid) in self._asked:
                continue
            if any(d["vid"] == vid and d["pid"] == pid
                   for d in self.devices):
                continue
            if not worth_offering([d for d in ifaces
                                   if d.vid == vid and d.pid == pid]):
                continue    # 主板灯效、虚拟设备、读不了电量的接收器：不打扰
            self._asked.add((vid, pid))
            self._pending_add = (vid, pid)
            break

    def _auto_add(self, ifaces: list, keys) -> int:
        """内置适配器认得的设备直接加入面板，不弹确认框（参考 Voltide：接入即出现）。
        用户删过的设备记在 dismissed 里，不再自动加回来；重新插拔时仍会弹框询问。"""
        dismissed = set(load_config().get("dismissed") or [])
        have = {(d["vid"], d["pid"]) for d in self.devices}
        added = 0
        for vid, pid in sorted(keys):
            if (vid, pid) in have or f"{vid:04X}:{pid:04X}" in dismissed:
                continue
            cand = [d for d in ifaces if d.vid == vid and d.pid == pid]
            found = adapters_for(cand)
            if not found:
                continue
            self.devices.append({"vid": vid, "pid": pid,
                                 "name": found[0].model_name(cand)})
            self._asked.add((vid, pid))
            added += 1
        if added:
            save_saved_devices(self.devices)
            self.refresh()
        return added

    def auto_add_known(self) -> int:
        try:
            ifaces = enumerate_hid()
        except Exception:
            return 0
        return self._auto_add(ifaces, {(d.vid, d.pid) for d in ifaces})

    def _show_add(self, vid: int, pid: int):
        try:
            ifaces = enumerate_hid()
            r = read_saved_device({"vid": vid, "pid": pid, "name": ""},
                                  ifaces)
            name = r.name or f"{vid:04X}:{pid:04X}"
            level = r.level if r.ok else None
            self._add_win = AddDeviceWindow(self, vid, pid, name, level,
                                            r.device_type)
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
        cfg = load_config()
        dismissed = cfg.get("dismissed")
        if isinstance(dismissed, list) and f"{vid:04X}:{pid:04X}" in dismissed:
            dismissed.remove(f"{vid:04X}:{pid:04X}")
            save_config(cfg)
        self.refresh()

    def remove_device(self, key: str):
        if key.startswith(("bt_", "xi_")):
            # 蓝牙设备和手柄不是手动添加的，“删除”就是不再显示
            cfg = load_config()
            hidden = list(cfg.get("bt_hidden") or [])
            if key not in hidden:
                hidden.append(key)
            cfg["bt_hidden"] = hidden
            save_config(cfg)
            with self._lock:
                self._readings.pop(key, None)
            self.refresh()
            return
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
            dismissed = [k for k in (cfg.get("dismissed") or [])
                         if isinstance(k, str)]
            dk = f"{removed['vid']:04X}:{removed['pid']:04X}"
            if dk not in dismissed:
                dismissed.append(dk)
            cfg["dismissed"] = dismissed
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
            self._poll_temps()
            if self._temps_new:
                self._temps_new = False
                self.panel.update_temps()
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

