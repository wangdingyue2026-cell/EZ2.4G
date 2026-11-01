"""抓包解析 / 协议推断 / 协议回读的离线测试：合成 USBPcap 文件 + 假 HID 设备。

用法：python ci/test_protocol.py my_devices.py
非 Windows 上会把 Win32 DLL 换成空桩，只为能导入模块跑纯逻辑。
"""
import ctypes
import importlib.util
import json
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252
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


# -- 内置设备适配器 ----------------------------------------------------------------
def hid(path, vid, pid, page, inp=0, out=0, feat=0, usage=1, product=""):
    return md.HidIface(path=path, vid=vid, pid=pid, usage_page=page,
                       usage=usage, input_len=inp, output_len=out,
                       feature_len=feat, product=product)


def spec(sid):
    return next(s for s in md.DEVICE_SPECS if s["id"] == sid)


def H(s):
    return bytes.fromhex(s.replace(" ", ""))


def claimed(ifaces):
    return [a.id for a in md.adapters_for(ifaces)]


def jl_reply(payload):
    f = bytearray(65)
    f[1], f[3], f[5] = 0xFE, 0x10, 0x02
    f[8] = len(payload)
    f[10:10 + len(payload)] = payload
    return bytes(f)


def memory_config(store):
    md.load_config = lambda: json.loads(json.dumps(store))
    md.save_config = lambda cfg: (store.clear(), store.update(cfg))


@case
def compx_frame_and_real_replies():
    """ATK / IMECOO 17 字节：查询帧校验和 0x49；两条真机回复都能解析（含毫伏和校验）。"""
    f = md.build_spec_frames(spec("compx17"))[0]
    assert f == H("08 04" + " 00" * 14 + " 49"), f.hex(" ")
    atk = H("08 04 00 00 00 02 64 00 10 DD 00 00 00 00 00 00 F6")
    imecoo = H("08 04 00 00 00 02 1E 00 0E 5A 00 00 00 00 00 00 C1")
    assert md.parse_spec_reply(spec("compx17"), atk) == (100, "discharging", 4317)
    assert md.parse_spec_reply(spec("compx17"), imecoo) == (30, "discharging", 3674)
    bad = bytearray(atk)
    bad[16] ^= 1
    assert md.parse_spec_reply(spec("compx17"), bytes(bad)) is None   # 校验不对
    rejected = bytearray(imecoo)
    rejected[2], rejected[16] = 1, (rejected[16] - 1) & 0xFF
    assert md.parse_spec_reply(spec("compx17"), bytes(rejected)) is None


@case
def compx_read_uses_set_report_first():
    """ATK A9 拒收 WriteFile，先走 SET_REPORT；回复前混一条别的报告不采信。"""
    SCRIPTS["a9"] = {"input": [H("08 05" + " 00" * 15),
                               H("08 04 00 00 00 02 50 01 10 00" + " 00" * 6 + " 00")]}
    rep = bytearray(SCRIPTS["a9"]["input"][1])
    rep[16] = (0x4D - sum(rep[1:16])) & 0xFF
    SCRIPTS["a9"]["input"][1] = bytes(rep)
    ifs = [hid("a9", 0x373B, 0x1278, 0xFF02, 17, 17)]
    a = next(x for x in md.adapters_for(ifs) if x.id == "compx17")
    r = a.read(ifs)
    assert r.ok and r.level == 80 and r.status == "charging", r
    assert FakeDev.log[0][0] == "set_out", FakeDev.log
    assert a.model_name(ifs) == "ATK A9 Air 大师+"


@case
def shared_vid_3554_isolation():
    """3554 是 CompX 共用号段：迈从键盘 20/20 只归迈从；VGN 17/17、未知 PID 谁都不认领。"""
    mchose = [hid("k", 0x3554, 0xFA09, 0xFF02, 20, 20)]
    vgn = [hid("v", 0x3554, 0xF503, 0xFF02, 17, 17)]
    other = [hid("o", 0x3554, 0x1234, 0xFF02, 33, 33)]
    assert claimed(mchose) == ["mchose20"], claimed(mchose)
    assert claimed(vgn) == [], claimed(vgn)
    assert claimed(other) == [], claimed(other)
    assert claimed([hid("i", 0x3554, 0xFB34, 0xFF02, 17, 17)]) == ["compx17"]


@case
def mchose_frame_and_reply():
    f = md.build_spec_frames(spec("mchose20"))[0]
    assert len(f) == 20 and f[:4] == H("13 4A 01 00") and f[19] == 0x5E, f.hex(" ")
    rep = bytearray(20)
    rep[:7] = H("13 4A 01 00 02 64 01")
    rep[19] = sum(rep[:19]) & 0xFF
    assert rep[19] == 0xC5                              # Voltide 的真机样本
    assert md.parse_spec_reply(spec("mchose20"), bytes(rep)) == (100, "discharging", None)
    rep[6] = 0x10
    rep[19] = sum(rep[:19]) & 0xFF
    assert md.parse_spec_reply(spec("mchose20"), bytes(rep))[1] == "charging"


@case
def single_frame_specs_parse_real_samples():
    cases = {
        "rog_omni": (H("02 12 01 00 00 00 30 02 01 00 14 30 ED 0E"), 48),
        "atk_n9": (H("55 65 64 02 02 00 00 04"), 100),
        "atk64": (H("08 72 02 00 01 07 01 4B"), 75),
        "inphic_in10": (H("03 98 40 01 64"), 100),
        "darkrim": (jl_reply(b"\x01\x01\x46"), 70),
    }
    for sid, (rep, want) in cases.items():
        got = md.parse_spec_reply(spec(sid), rep)
        assert got and got[0] == want, (sid, got)
    full = md.parse_spec_reply(spec("atk_n9"), H("55 65 5A 04"))
    assert full == (100, "full", None), full
    bs = bytearray(64)
    bs[:4] = H("02 0E 50 49")
    bs[12:16] = H("21 01 01 4B")
    assert md.parse_spec_reply(spec("razer_bs"), bytes(bs))[0] == 75
    assert md.parse_spec_reply(spec("darkrim"), jl_reply(b"\x02\x03ABC")) is None


@case
def inphic_in10_is_listen_only():
    """IN10 只听不写：只打开读句柄，等鼠标自己每 2 秒推的电量帧。"""
    SCRIPTS["in10"] = {"input": [H("03 98 20 01 00"), H("03 98 40 01 3C")]}
    ifs = [hid("in10", 0x045E, 0x0098, 0x000A, 5)]
    assert claimed(ifs) == ["inphic_in10"]
    assert claimed([hid("ms", 0x045E, 0x0040, 0x000A, 5)]) == []   # 别的微软设备
    a = md.adapters_for(ifs)[0]
    r = a.read(ifs)
    assert r.ok and r.level == 60, r
    assert FakeDev.log == [], FakeDev.log


@case
def inphic_in9_handshake_then_query():
    s = spec("inphic_in9")
    rep = bytearray(33)
    rep[0], rep[1], rep[2], rep[6], rep[0x12], rep[0x13] = 0x0C, 1, 0x20, 1, 77, 2
    SCRIPTS["in9"] = {"feature": [bytes(rep)]}
    ifs = [hid("in9", 0x248A, 0x5B2F, 0xFF00, feat=33)]
    r = md.adapters_for(ifs)[0].read(ifs)
    assert r.ok and r.level == 77 and r.status == "charging", r
    sent = [e[1][:3] for e in FakeDev.log if e[0] == "set_feat"]
    assert sent == [H("0C 01 F0"), H("0C 01 20")], FakeDev.log
    assert claimed([hid("x", 0x248A, 0x1111, 0xFF00, feat=33)]) == []


@case
def rapoo_and_acer_and_vaxee():
    rap = bytearray(19)
    rap[0], rap[7], rap[8] = 0x07, 0x02, 85
    assert md.parse_spec_reply(spec("rapoo"), bytes(rap)) == (85, "charging", None)
    acer = bytearray(33)
    acer[1], acer[3], acer[14] = 0x10, 0x01, 64
    assert md.parse_spec_reply(spec("acer_omr"), bytes(acer))[0] == 64
    vx = bytearray(64)
    vx[:6] = H("0E A5 0B 00 00 11")
    assert md.parse_spec_reply(spec("vaxee"), bytes(vx))[0] == 85
    vx[5] = 30                                          # 超过 20 档：不采信
    assert md.parse_spec_reply(spec("vaxee"), bytes(vx)) is None


@case
def pixart_finds_reply_after_wait_frames():
    rep = bytearray(65)
    rep[1:9] = H("A1 00 02 02 00 83 01 2D")
    assert md.parse_spec_reply(spec("pixart83"), H("00 A0" + " 00" * 10)) is None
    assert md.parse_spec_reply(spec("pixart83"), bytes(rep)) == (45, "charging", None)


@case
def angry_miao_mouse_real_sample():
    """怒喵 INFINITY MOUSE .100 真机样本：Windows 偏移 3 = 72%；状态字节只认 00 / 01。"""
    rep = H("050000480100010200000164000100F4000100F300140000000A05"
            + "00" * 38)
    assert md.parse_spec_reply(spec("am_mouse"), rep)[0] == 72
    bad = bytearray(rep)
    bad[1] = 0x02
    assert md.parse_spec_reply(spec("am_mouse"), bytes(bad)) is None
    ifs = [hid("am", 0x3151, 0x5007, 0xFFFF, feat=65, usage=2)]
    assert claimed(ifs) == ["am_mouse"]
    assert claimed([hid("lb", 0x3151, 0x402D, 0xFFFF, feat=65, usage=2)]) == []


@case
def logitech_hidpp_unified_battery():
    """GPW3 真机抓包：0x1000 不存在 → 0x1004 功能 1，回复 11 01 06 1E 61 08 00 = 97%。"""
    ping = pad(H("11 01 00 1E 04 02 5A"), 20)
    no_1000 = pad(H("11 01 00 0E 00"), 20)
    has_1004 = pad(H("11 01 00 0E 06"), 20)
    status = pad(H("11 01 06 1E 61 08 00"), 20)
    noise = pad(H("11 01 04 00 01 02 03"), 20)
    SCRIPTS["logi"] = {"input": [ping, no_1000, noise, has_1004, status]}
    ifs = [hid("logi", 0x046D, 0xC547, 0xFF00, 20, 20, usage=2,
               product="USB Receiver")]
    assert claimed(ifs) == ["logitech"]
    r = md.adapters_for(ifs)[0].read(ifs)
    assert r.ok and r.level == 97 and r.status == "discharging", r
    writes = [e[1][:4] for e in FakeDev.log if e[0] == "write"]
    assert writes[0] == H("11 01 00 1E") and writes[-1] == H("11 01 06 1E"), writes
    wired = [hid("w", 0x046D, 0xC08B, 0xFF00, 20, 20, usage=2,
                 product="G502 HERO Gaming Mouse")]
    assert claimed(wired) == []                         # 有线设备不认领


@case
def razer_scales_raw_and_reads_charging():
    """雷蛇电量 0–255 一律换算（132 → 52%），充电走 0x84。"""
    def reply(tx, val):
        b = bytearray(91)
        b[1], b[2], b[6], b[10] = 2, tx, 2, val
        return bytes(b)
    SCRIPTS["rz"] = {"feature": [reply(0x20, 132), reply(0x30, 1)]}
    ifs = [hid("rz", 0x1532, 0x007B, 0x0001, feat=91)]
    r = md.adapters_for(ifs)[0].read(ifs)
    assert r.ok and r.level == 52 and r.status == "charging", r
    req = FakeDev.log[0][1]
    assert req[7:9] == H("07 80") and req[89] == req[3] ^ req[6] ^ 0x07 ^ 0x80 ^ 0
    crc = 0
    for v in req[3:89]:
        crc ^= v
    assert req[89] == crc
    assert claimed([hid("rk", 0x1532, 0x0999, 0x0001, feat=91)]) == []


@case
def unknown_device_gets_no_writes():
    """没有适配器认领的设备：不发任何帧，显示“协议未知”。"""
    store = {}
    memory_config(store)
    md.read_standard_battery = lambda d: None
    SCRIPTS["u"] = {}
    r = md.read_saved_device({"vid": 0x1234, "pid": 1, "name": ""},
                             [hid("u", 0x1234, 1, 0xFF00, 64, 64)])
    assert r.status == "unknown_proto" and FakeDev.log == [], (r, FakeDev.log)


@case
def adapter_reading_and_sleep_in_read_saved_device():
    store = {}
    memory_config(store)
    md.read_standard_battery = lambda d: None
    rep = bytearray(H("08 04 00 00 00 02 2D 00 0F A0" + " 00" * 7))
    rep[16] = (0x4D - sum(rep[1:16])) & 0xFF
    SCRIPTS["m"] = {"input": [bytes(rep)]}
    ifs = [hid("m", 0x373B, 0x11D9, 0xFF02, 17, 17)]
    dev = {"vid": 0x373B, "pid": 0x11D9, "name": ""}
    r = md.read_saved_device(dev, ifs)
    assert r.ok and r.level == 45 and r.source == "ATK 协议", r
    assert "protocols" not in store                     # 适配器不往配置里存协议
    SCRIPTS["m"] = {"input": []}
    r = md.read_saved_device(dev, ifs)
    assert r.status == "asleep" and r.source == "ATK 协议", r
    r = md.read_saved_device(dev, [])
    assert r.status == "unplugged", r


@case
def legacy_preset_is_fallback_only():
    """旧版自动保存的 preset 协议：有适配器时先用适配器，读不到才回退。"""
    store = {"protocols": {"373B:1216": {
        "kind": "preset", "v": 2, "requests": ["08 7D 72 02 00 01 07 01"],
        "req_kinds": ["out"], "offset": 7, "reply": "input",
        "match": [[0, 8], [1, 0x72], [5, 7]], "tries": 1, "timeout_ms": 100}}}
    memory_config(store)
    md.read_standard_battery = lambda d: None
    SCRIPTS["p"] = {"input": [pad(H("08 72 02 00 01 07 01 3C"), 64)]}
    ifs = [hid("p", 0x373B, 0x1216, 0xFF05, 64, 64)]
    r = md.read_saved_device({"vid": 0x373B, "pid": 0x1216, "name": ""}, ifs)
    assert r.ok and r.level == 60 and r.source == "ATK 协议", r


@case
def addable_devices_for_picker():
    """「添加设备」只列插着、没加过的接收器：能读电量的在前，普通键鼠和蓝牙不列。"""
    store = {"protocols": {"5555:0001": {"kind": "captured", "v": 2,
                                          "requests": ["06 01"], "offset": 3}}}
    memory_config(store)
    bt = md.dataclasses.replace(hid("b", 0x046D, 0xB023, 0xFF00, 20, 20),
                                instance="BTHENUM\\X")
    ifs = [hid("z", 0x9999, 0x0001, 0xFF00, 64, 64, product="Zeta Receiver"),
           hid("a", 0x373B, 0x1278, 0xFF02, 17, 17),
           hid("k", 0x1111, 0x2222, 0x0001, 8),            # 普通有线键盘
           hid("s", 0x5555, 0x0001, 0x0001, 8),            # 保存过协议
           hid("d", 0x3654, 0x6901, 0xFF02, 65, 65),       # 已在面板里
           bt]
    got = md.addable_devices(ifs, [{"vid": 0x3654, "pid": 0x6901, "name": ""}])
    assert [(g["vid"], g["supported"]) for g in got] == [
        (0x373B, True), (0x5555, True), (0x9999, False)], got
    assert got[0]["name"] == "ATK A9 Air 大师+"
    assert got[2]["name"] == "Zeta Receiver"


# -- 导入设备规格（声明式适配器，带真机样本） ----------------------------------------
GOOD_SPEC = {"name": "示例 鼠标", "kind": "mouse", "ids": [["ABCD", "0001"]],
             "page": "0xFF02", "lens": {"in": 17, "out": 17},
             "send": ["setout"], "frames": ["08 04"],
             "csum": {"kind": "const", "const": 77, "from": 1, "to": 15, "at": 16},
             "reply": "input", "expect": [[0, 8], [1, 4]], "level": [6],
             "sample": "08 04 00 00 00 02 50 00 00 00 00 00 00 00 00 00 25"}


@case
def spec_import_requires_working_sample():
    store = {}
    memory_config(store)
    got = md.import_protocols(json.dumps({"specs": [GOOD_SPEC]}))
    assert list(got) == ["spec:示例 鼠标"], got
    assert store["specs"][0]["ids"] == [["ABCD", "0001"]], store
    ifs = [hid("s", 0xABCD, 0x0001, 0xFF02, 17, 17)]
    assert claimed(ifs)[0] == "user:示例 鼠标", claimed(ifs)
    assert md.adapter_knows(0xABCD, 0x0001)
    for patch, why in ((dict(sample="08 04 00 00 00 02 FF"), "sample"),
                       (dict(ids=[["ABCD", None]]), "PID"),
                       (dict(expect=[]), "expect"),
                       (dict(send=["poke"]), "send"),
                       (dict(sample=""), "sample")):
        bad = dict(GOOD_SPEC, **patch)
        try:
            md.parse_import(json.dumps({"specs": [bad]}))
            raise AssertionError(patch)
        except ValueError as e:
            assert why in str(e), (patch, str(e))


@case
def device_info_and_xinput():
    store = {}
    memory_config(store)
    r = md.Reading(ok=True, level=45, name="ATK F1", source="ATK 协议",
                   raw=H("08 04 00"))
    r.vid, r.pid = 0x373B, 0x11D9
    text = md.device_info_text(r, [hid("m", 0x373B, 0x11D9, 0xFF02, 17, 17)])
    assert "373B:11D9" in text and "ATK 无线鼠标" in text and "08 04 00" in text, text
    assert md.read_xinput_all() == {}                   # 非 Windows 下不读


# -- 导入 / 导出协议 ----------------------------------------------------------------
@case
def import_export_round_trip():
    store = {"devices": [{"vid": VID, "pid": PID, "name": "我的鼠标"}],
             "protocols": {f"{VID:04X}:{PID:04X}": {
                 "kind": "captured", "v": 2, "requests": ["06 01"],
                 "req_kinds": ["out"], "offset": 3, "reply": "input",
                 "match": [[0, 6]], "invert": False},
                 "AAAA:BBBB": {"kind": "standard"}},
             "last": {md.device_key(VID, PID): {"level": 10}}}
    md.load_config = lambda: json.loads(json.dumps(store))
    md.save_config = lambda cfg: (store.clear(), store.update(cfg))
    text = md.export_protocols_text()
    data = json.loads(text)
    assert list(data["protocols"]) == [f"{VID:04X}:{PID:04X}"], data
    assert data["protocols"][f"{VID:04X}:{PID:04X}"]["name"] == "我的鼠标"
    store.clear()
    got = md.import_protocols(text, add_devices=True)
    p = store["protocols"][f"{VID:04X}:{PID:04X}"]
    assert list(got) == [f"{VID:04X}:{PID:04X}"]
    assert p["source"] == "import" and p["requests"] == ["06 01"], p
    assert md.protocol_source(p) == "导入协议"
    assert store["devices"] == [{"vid": VID, "pid": PID, "name": ""}], store


@case
def import_single_protocol_needs_device():
    single = json.dumps({"requests": ["06 01"], "offset": 3})
    try:
        md.parse_protocol_import(single)
        raise AssertionError("应当要求指明设备")
    except ValueError as e:
        assert "右键" in str(e), e
    out = md.parse_protocol_import(single, "1A2B:3C4D")
    assert out["1A2B:3C4D"]["kind"] == "captured", out


@case
def import_rejects_bad_files():
    bad = [
        ("不是 JSON", "JSON"),
        ('[1, 2]', "格式不对"),
        ('{"protocols": {"12:34": {"requests": ["06 01"]}}}', "设备编号"),
        ('{"protocols": {"1234:5678": {"requests": ["zz"]}}}', "十六进制"),
        ('{"protocols": {"1234:5678": {"requests": ["06 01"], '
         '"offset": -1}}}', "offset"),
        ('{"protocols": {"1234:5678": {"kind": "evil"}}}', "类型"),
        ('{"protocols": {}}', "没有协议"),
    ]
    for text, why in bad:
        try:
            md.parse_protocol_import(text)
            raise AssertionError(text)
        except ValueError as e:
            assert why in str(e), (text, str(e))
    # 认识的字段之外一律丢弃
    out = md.parse_protocol_import(
        '{"1234:5678": {"requests": ["06 01"], "offset": 3, "evil": 1}}')
    assert "evil" not in out["1234:5678"], out


@case
def cli_import_file():
    store = {}
    md.load_config = lambda: json.loads(json.dumps(store))
    md.save_config = lambda cfg: (store.clear(), store.update(cfg))
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:  # 记事本存的带 BOM
        f.write('{"protocols": {"373b:1216": {"requests": ["08 7D"], '
                '"offset": 7, "v": 2}}}')
    try:
        assert md.main(["--import", path]) == 0
        assert md.main(["--import"]) == 2
    finally:
        os.remove(path)
    assert "373B:1216" in store["protocols"], store
    assert store["devices"][0]["vid"] == 0x373B, store


print(f"\n{len(passed)} protocol tests passed")
