"""抓包解析 / 协议推断 / 协议回读的离线测试：合成 USBPcap 文件 + 假 HID 设备。

用法：python ci/test_protocol.py my_devices.py
非 Windows 上会把 Win32 DLL 换成空桩，只为能导入模块跑纯逻辑。
"""
import ctypes
import importlib.util
import os
import struct
import sys
import tempfile
import types

if sys.platform != "win32":
    class _Fn:
        def __call__(self, *a, **k):
            return 0

    class _Dll:
        def __init__(self, *a, **k):
            self._f = {}

        def __getattr__(self, n):
            if n.startswith("__"):
                raise AttributeError(n)
            return self._f.setdefault(n, _Fn())

        def __getitem__(self, i):
            return _Fn()
    ctypes.WinDLL = _Dll
    ctypes.windll = _Dll()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE

spec = importlib.util.spec_from_file_location("my_devices", sys.argv[1])
md = importlib.util.module_from_spec(spec)
sys.modules["my_devices"] = md
spec.loader.exec_module(md)

VID, PID = 0x1A2B, 0x3C4D


# -- 合成 USBPcap --------------------------------------------------------------
def pkt(ep, transfer, payload, stage=None, bus=1, dev=5):
    hlen = 27 if stage is None else 28
    hdr = struct.pack("<HQIHBHHBBI", hlen, 0, 0, 0, 0, bus, dev, ep,
                      transfer, len(payload))
    if stage is not None:
        hdr += bytes((stage,))
    body = hdr + bytes(payload)
    return struct.pack("<IIII", 0, 0, len(body), len(body)) + body


def device_descriptor(bus=1, dev=5):
    desc = bytes((18, 1, 0, 2, 0, 0, 0, 64)) + struct.pack("<HH", VID, PID) \
        + bytes(6)
    return pkt(0x80, 2, desc, stage=1, bus=bus, dev=dev)


def write_pcap(packets):
    fd, path = tempfile.mkstemp(suffix=".pcap")
    with os.fdopen(fd, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 249))
        for p in packets:
            f.write(p)
    return path


def frames_of(packets):
    path = write_pcap(packets)
    try:
        frames, _mapping = md.extract_pcap_for(path, VID, PID)
        return frames
    finally:
        os.remove(path)


# -- 假 HID 设备 ----------------------------------------------------------------
class FakeDev:
    log = []

    def __init__(self, script):
        self.s = script

    def close(self):
        pass

    def drain(self):
        pass

    def write_output(self, data):
        FakeDev.log.append(("write", bytes(data)))
        return len(data)

    def set_output_report(self, data):
        FakeDev.log.append(("set_out", bytes(data)))
        return True

    def set_feature(self, data):
        FakeDev.log.append(("set_feat", bytes(data)))
        return True

    def read_input(self, length, timeout_ms=800):
        q = self.s.get("input", [])
        return q.pop(0) if q else None

    def get_feature_report(self, rid, length):
        FakeDev.log.append(("get_feat", rid, length))
        q = self.s.get("feature", [])
        return q.pop(0) if q else None

    def get_input_report(self, rid, length):
        q = self.s.get("get_input", [])
        return q.pop(0) if q else None


SCRIPTS = {}
md.HidDevice = lambda path, write=False: FakeDev(SCRIPTS[path])


def iface(path, inp=0, out=0, feat=0):
    return md.HidIface(path=path, vid=VID, pid=PID, usage_page=0xFF00,
                       usage=1, input_len=inp, output_len=out,
                       feature_len=feat)


def pad(b, n):
    return bytes(b) + bytes(n - len(b))


passed = []


def case(fn):
    FakeDev.log = []
    SCRIPTS.clear()
    fn()
    passed.append(fn.__name__)
    print("ok ", fn.__name__)
    return fn


# -- 用例 ------------------------------------------------------------------------
@case
def interrupt_with_report_id():
    """带报告 ID 的中断传输：只认回复特征，别的报告里 0–100 的字节不采信。"""
    req = pad((0x06, 0x01), 17)
    reply = pad((0x06, 0x01, 0x00, 75), 17)
    noise = pad((0x07, 0x22, 0x33, 0x44, 0x10), 17)
    frames = frames_of([device_descriptor(), pkt(0x81, 1, noise),
                        pkt(0x02, 1, req), pkt(0x81, 1, reply)])
    proto, why = md.infer_single_ex(75, frames)
    assert why == "ok", why
    assert proto["offset"] == 3 and proto["v"] == 2, proto
    assert proto["req_kinds"] == ["out"] and proto["reply"] == "input", proto
    assert proto["in_len"] == 17 and proto["out_len"] == 17, proto
    assert proto["match"] == [[0, 6], [1, 1]], proto
    SCRIPTS["a"] = {"input": [pad((0x07, 0x00, 0x00, 50), 17),
                              pad((0x06, 0x01, 0x00, 80), 17)]}
    r = md._read_user_protocol([iface("a", 17, 17)], proto)
    assert r and r.ok and r.level == 80, r
    assert FakeDev.log == [("write", req)], FakeDev.log


@case
def interrupt_without_report_id():
    """设备不用报告 ID：线上帧比 Windows 缓冲区少 1 字节，写入补 0、读取偏移 +1。"""
    req = pad((0x55, 0x01), 16)
    reply = pad((0xAA, 0x01, 0x00, 75), 16)
    frames = frames_of([device_descriptor(), pkt(0x02, 1, req),
                        pkt(0x81, 1, reply)])
    proto, why = md.infer_single_ex(75, frames)
    assert why == "ok" and proto["offset"] == 3 and proto["in_len"] == 16
    SCRIPTS["b"] = {"input": [b"\0" + pad((0xAA, 0x01, 0x00, 64), 16)]}
    r = md._read_user_protocol([iface("b", 17, 17)], proto)
    assert r and r.level == 64, r
    assert FakeDev.log == [("write", b"\0" + req)], FakeDev.log


@case
def feature_report_split_stages():
    """SET_REPORT(Feature) + GET_REPORT(Feature)，setup 与数据分包记录。"""
    set_setup = bytes((0x21, 0x09, 0x05, 0x03, 0x02, 0x00, 0x08, 0x00))
    set_data = pad((0x05, 0x10), 8)
    get_setup = bytes((0xA1, 0x01, 0x06, 0x03, 0x02, 0x00, 0x08, 0x00))
    get_data = pad((0x06, 0x00, 75, 0x01), 8)
    frames = frames_of([
        device_descriptor(),
        pkt(0x00, 2, set_setup, stage=0), pkt(0x00, 2, set_data, stage=1),
        pkt(0x80, 2, b"", stage=2),
        pkt(0x80, 2, get_setup, stage=0), pkt(0x80, 2, get_data, stage=1),
    ])
    kinds = [tr for _ep, tr, _d in frames]
    assert md.XFER_SET_FEATURE in kinds and md.XFER_GET_FEATURE in kinds, \
        frames
    proto, why = md.infer_single_ex(75, frames)
    assert why == "ok", why
    assert proto["req_kinds"] == ["set_feat"], proto
    assert proto["requests"][0].startswith("05 10"), proto
    assert proto["reply"] == "feature" and proto["reply_id"] == 6, proto
    assert proto["offset"] == 2 and proto["match"] == [[0, 6], [1, 0]], proto
    SCRIPTS["c"] = {"feature": [pad((0x06, 0x00, 90, 0x01), 8)]}
    r = md._read_user_protocol([iface("c", feat=8)], proto)
    assert r and r.level == 90, r
    assert FakeDev.log[0] == ("set_feat", set_data), FakeDev.log
    assert FakeDev.log[1] == ("get_feat", 6, 8), FakeDev.log


@case
def set_output_combined_stage():
    """旧版 USBPcap：SET_REPORT(Output) 的 setup 与数据同包；报告 ID 为 0 时补 0。"""
    setup = bytes((0x21, 0x09, 0x00, 0x02, 0x01, 0x00, 0x04, 0x00))
    reply = pad((0x09, 0x01, 75), 8)
    frames = frames_of([device_descriptor(),
                        pkt(0x00, 2, setup + bytes((1, 2, 3, 4))),
                        pkt(0x81, 1, reply)])
    proto, why = md.infer_single_ex(75, frames)
    assert why == "ok" and proto["req_kinds"] == ["set_out"], proto
    assert proto["requests"][0] == "00 01 02 03 04", proto
    SCRIPTS["d"] = {"input": [b"\0" + pad((0x09, 0x01, 42), 8)]}
    r = md._read_user_protocol([iface("d", 9, 5)], proto)
    assert r and r.level == 42, r
    assert FakeDev.log == [("set_out", bytes((0, 1, 2, 3, 4)))], FakeDev.log


@case
def two_captures_disambiguate():
    """单次有两个位置都等于电量，第二次抓包按差值锁定唯一位置。"""
    req = pad((0x06, 0x01), 17)
    a = frames_of([device_descriptor(), pkt(0x02, 1, req),
                   pkt(0x81, 1, pad((0x06, 0x01, 0, 75, 0, 75), 17))])
    b = frames_of([device_descriptor(), pkt(0x02, 1, req),
                   pkt(0x81, 1, pad((0x06, 0x01, 0, 60, 0, 33), 17))])
    assert md.infer_single_ex(75, a)[1] == "ambiguous"
    proto, why = md.infer_two(75, a, 60, b)
    assert why == "ok" and proto["offset"] == 3, (why, proto)


@case
def invert_and_listen():
    """反向协议取 100-值；被动监听只接受与回复特征完全吻合的报告。"""
    proto = {"v": 2, "requests": ["06 01"], "req_kinds": ["out"],
             "out_len": 17, "offset": 3, "reply": "input", "in_len": 17,
             "match": [[0, 6], [1, 1]], "invert": True}
    SCRIPTS["e"] = {"input": [pad((0x07, 0x01, 0, 10), 17),
                              pad((0x06, 0x02, 0, 20), 17),
                              pad((0x06, 0x01, 0, 30), 17)]}
    r = md._listen_user_protocol([iface("e", 17, 17)], proto, seconds=0.5)
    assert r and r.level == 70, r


@case
def legacy_protocol_unchanged():
    """没有 v 字段的旧协议仍走原来的写 Output / 读 Input 路径。"""
    proto = {"kind": "captured", "requests": ["06 01"], "offset": 3}
    SCRIPTS["f"] = {"input": [pad((0x06, 0x01, 0, 55), 17)]}
    r = md._read_user_protocol([iface("f", 17, 17)], proto)
    assert r and r.level == 55, r
    assert FakeDev.log == [("write", pad((0x06, 0x01), 17))], FakeDev.log


@case
def save_keeps_new_fields():
    store = {}
    md.load_config = lambda: dict(store)
    md.save_config = lambda cfg: store.update(cfg)
    md.save_user_protocol(VID, PID, {
        "v": 2, "requests": ["06 01"], "req_kinds": ["out"], "offset": 3,
        "reply": "input", "in_len": 17, "match": [[0, 6]], "out_len": 17,
        "junk": 1})
    saved = store["protocols"][f"{VID:04X}:{PID:04X}"]
    assert saved["v"] == 2 and saved["match"] == [[0, 6]], saved
    assert saved["kind"] == "captured" and "junk" not in saved, saved


@case
def format_reports_handles_synthetic():
    text = md.format_reports([(md.EP_CTRL_OUT, md.XFER_SET_FEATURE, b"\x05\x10"),
                              (0x81, 1, b"\x06\x01\x00\x4b")])
    assert "len=2" in text and "06 01 00 4B" in text, text


print(f"\n{len(passed)} protocol tests passed")
