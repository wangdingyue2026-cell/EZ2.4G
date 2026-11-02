"""蓝牙电量的测试：地址解析、多节点合并、隐藏/改名/开关；Windows 上再真调一次系统 API。

用法：python ci/test_bluetooth.py my_devices.py
"""
import ctypes
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252

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

HFP = ("BTHENUM\\{0000111E-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
       "\\7&2A3B4C5D&0&001A7DDA7113_C00000000")
A2DP = ("BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        "\\7&2A3B4C5D&0&001A7DDA7113_C00000000")
LE = "BTHLE\\DEV_C1D2E3F4A5B6\\8&1234ABCD&0&C1D2E3F4A5B6"
LE_GATT = ("BTHLEDEVICE\\{00002A19-0000-1000-8000-00805F9B34FB}_DEV_VID&02045E"
           "_PID&0B13_REV&0001_C1D2E3F4A5B6\\9&ABCDEF&0&0018")

passed = []


def case(fn):
    fn()
    passed.append(fn.__name__)
    print("ok ", fn.__name__)
    return fn


@case
def address_from_instance_id():
    assert md._bt_address(HFP) == "001A7DDA7113"
    assert md._bt_address(LE) == "C1D2E3F4A5B6"
    assert md._bt_address(LE_GATT) == "C1D2E3F4A5B6"
    assert md._bt_address("BTHENUM\\WHATEVER") == ""


@case
def merge_nodes_of_same_device():
    """耳机的免提和音频两个节点合成一张卡；断开的设备不显示。"""
    nodes = [
        {"inst": HFP, "level": 80, "name": "", "container": "aa"},
        {"inst": A2DP, "level": 80, "name": "WH-1000XM5", "container": "aa"},
        {"inst": LE, "level": 45, "name": "Surface Pen", "container": "bb"},
        {"inst": LE_GATT, "level": 45, "name": "", "container": "bb"},
        {"inst": "BTHLE\\DEV_0011223344AA\\x", "level": 10, "name": "Old Mouse",
         "container": "cc"},
    ]
    res = md._bt_merge(nodes, {"001A7DDA7113": True,
                               "0011223344AA": False})
    assert set(res) == {"bt_001a7dda7113", "bt_c1d2e3f4a5b6"}, res
    hp = res["bt_001a7dda7113"]
    assert hp.name == "WH-1000XM5" and hp.level == 80 and hp.ok, hp
    assert hp.kind == "bluetooth" and hp.detail == "已连接", hp
    pen = res["bt_c1d2e3f4a5b6"]
    assert pen.level == 45 and pen.detail == "", pen  # 经典 API 查不到 → 不标已连接


@case
def hidden_renamed_and_switch():
    store = {"names": {"bt_c1d2e3f4a5b6": "我的手写笔"},
             "bt_hidden": ["bt_001a7dda7113"]}
    md.load_config = lambda: dict(store)
    md._bt_scan = lambda: ([
        {"inst": HFP, "level": 80, "name": "WH-1000XM5", "container": "aa"},
        {"inst": LE, "level": 45, "name": "Surface Pen", "container": "bb"}],
        [])
    md._bt_connected = lambda: {}
    res = md.read_bluetooth_all()
    assert list(res) == ["bt_c1d2e3f4a5b6"], res
    assert res["bt_c1d2e3f4a5b6"].name == "我的手写笔", res
    store["bluetooth"] = False
    assert md.read_bluetooth_all() == {}


@case
def subtitle_for_bluetooth():
    """面板副标题：蓝牙设备显示“蓝牙 · 已连接”，低电量显示“蓝牙 · 电量低”。"""
    r = md.Reading(ok=True, level=80, detail="已连接", kind="bluetooth")
    assert md.Panel._subtitle(None, r) == ("蓝牙 · 已连接", False)
    r = md.Reading(ok=True, level=12, kind="bluetooth")
    assert md.Panel._subtitle(None, r) == ("蓝牙 · 电量低", False)


# AirPods：Windows 里的配对节点（服务节点带苹果 Device ID）+ 收到的广播
AP_ADDR = "A1B2C3D4E5F6"
AP_DEV = "BTHENUM\\DEV_A1B2C3D4E5F6\\7&1111&0&BLUETOOTHDEVICE_A1B2C3D4E5F6"
AP_A2DP = ("BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_VID&0001004C"
           "_PID&2027\\7&2222&0&A1B2C3D4E5F6_C00000000")
AP_NODES = [{"inst": AP_A2DP, "name": "小明的 AirPods Pro Stereo"},
            {"inst": AP_DEV, "name": "小明的 AirPods Pro"}]


def ap_adv(model=0x2027, status=0x20, pods=0x98, flags=0x05):
    """拼一条「接近配对」广播：左耳 80%、右耳 90%、盒 50%，来自左耳。"""
    body = bytes([0x01, model & 0xFF, model >> 8, status, pods, flags,
                  0x31, 0x00, 0x05]) + bytes(16)
    return bytes([0x07, len(body)]) + body


@case
def airpods_advert_parsing():
    p = md.parse_airpods(ap_adv())
    assert p["model"] == 0x2027, hex(p["model"])
    assert (p["left"], p["right"], p["case"]) == (80, 90, 50), p
    assert not p["left_charging"] and not p["right_charging"]
    # 广播来自右耳：两个半字节对调
    p = md.parse_airpods(ap_adv(status=0x00))
    assert (p["left"], p["right"]) == (90, 80), p
    # 右耳在盒里充电（标志位跟着“当前耳机 / 另一只”走）
    p = md.parse_airpods(ap_adv(flags=0x25))
    assert p["right_charging"] and not p["left_charging"], p
    # 只剩一只耳机在线（另一只 15 = 不在线），充电盒合着
    p = md.parse_airpods(ap_adv(pods=0xF7, flags=0x0F))
    assert (p["left"], p["right"], p["case"]) == (70, None, None), p
    # 别的苹果广播类型 / 太短：不认
    assert md.parse_airpods(bytes([0x10, 0x05, 1, 2, 3, 4, 5])) is None
    assert md.parse_airpods(ap_adv()[:8]) is None


@case
def airpods_reading_text():
    r = md.airpods_reading(md.parse_airpods(ap_adv()), "AirPods Pro")
    assert r.level == 80 and r.ok and r.kind == "bluetooth", r
    assert r.device_type == "headset" and r.status == "bluetooth", r
    assert r.detail == "左 80% · 右 90% · 盒 50%", r.detail
    assert md.Panel._subtitle(None, r) == ("蓝牙 · 左 80% · 右 90% · 盒 50%",
                                           False)
    both = md.airpods_reading(md.parse_airpods(ap_adv(flags=0x35)), "x")
    assert both.status == "charging", both
    one = md.airpods_reading(md.parse_airpods(ap_adv(pods=0xF7)), "x")
    assert one.level == 70 and one.detail == "盒 50%", one
    assert md.airpods_reading(md.parse_airpods(ap_adv(pods=0xFF)), "x") is None


@case
def airpods_match_paired_model_only():
    """只给已连接的配对耳机找同型号广播；邻座别的型号、过期的、断开的都不显示。"""
    now = 1000.0
    adverts = {
        1: (now - 5, -80, md.parse_airpods(ap_adv(model=0x2014, pods=0x11))),
        2: (now - 2, -50, md.parse_airpods(ap_adv())),
        3: (now - 3, -90, md.parse_airpods(ap_adv(pods=0x22))),   # 远处同型号
    }
    res = md._airpods_merge(AP_NODES, {AP_ADDR: True}, adverts, {}, now)
    key = "bt_" + AP_ADDR.lower()
    assert list(res) == [key], res
    assert res[key].name == "小明的 AirPods Pro", res[key].name
    assert res[key].level == 80, res[key]
    # 只有别的型号的广播
    assert md._airpods_merge(AP_NODES, {AP_ADDR: True}, {1: adverts[1]},
                             {}, now) == {}
    # 广播太旧
    assert md._airpods_merge(AP_NODES, {AP_ADDR: True},
                             {2: (now - 200, -50, adverts[2][2])}, {},
                             now) == {}
    # Windows 说已断开（耳机连着手机）
    assert md._airpods_merge(AP_NODES, {AP_ADDR: False}, adverts, {}, now) == {}
    # Windows 自己给了电量：不重复
    assert md._airpods_merge(AP_NODES, {AP_ADDR: True}, adverts,
                             {key: md.Reading()}, now) == {}
    # 名字带 AirPods 但没有 Device ID：不看型号，只认离得近的
    named = [{"inst": AP_DEV, "name": "AirPods Pro"}]
    res = md._airpods_merge(named, {}, {3: adverts[3]}, {}, now)
    assert res == {}, res
    res = md._airpods_merge(named, {}, adverts, {}, now)
    assert res[key].level == 80, res


@case
def airpods_nodes_detected():
    assert md.is_apple_audio(AP_A2DP, "")
    assert md.is_apple_audio(AP_DEV, "Beats Studio Buds")
    assert not md.is_apple_audio(HFP, "WH-1000XM5")


@case
def airpods_in_read_bluetooth_all():
    store = {}
    md.load_config = lambda: dict(store)
    md._bt_scan = lambda: ([], AP_NODES)
    md._bt_connected = lambda: {AP_ADDR: True}
    md.AIRPODS.started = True                  # 不真的起后台线程
    md.AIRPODS.adverts = {2: (md.time.time(), -50,
                              md.parse_airpods(ap_adv()))}
    res = md.read_bluetooth_all()
    assert list(res) == ["bt_" + AP_ADDR.lower()], res
    store["bt_hidden"] = ["bt_" + AP_ADDR.lower()]
    assert md.read_bluetooth_all() == {}
    store.clear()
    md._bt_connected = lambda: {AP_ADDR: False}
    assert md.read_bluetooth_all() == {}


if sys.platform == "win32":
    # 上面的用例替换过模块里的函数，这里重新加载一份真实的
    spec2 = importlib.util.spec_from_file_location("my_devices_real",
                                                   sys.argv[1])
    real = importlib.util.module_from_spec(spec2)
    sys.modules["my_devices_real"] = real
    spec2.loader.exec_module(real)

    @case
    def struct_layout_matches_windows_sdk():
        assert ctypes.sizeof(real.SP_DEVINFO_DATA) == 32
        assert ctypes.sizeof(real.DEVPROPKEY) == 20
        assert ctypes.sizeof(real.BLUETOOTH_DEVICE_SEARCH_PARAMS) == 40
        assert ctypes.sizeof(real.BLUETOOTH_DEVICE_INFO) == 560

    @case
    def real_windows_calls_do_not_fail():
        nodes = real._bt_nodes()
        conn = real._bt_connected()
        res = real.read_bluetooth_all()
        print(f"    这台机器：{len(nodes)} 个带电量的蓝牙节点，"
              f"{len(conn)} 台已配对，显示 {len(res)} 台")

    @case
    def winrt_advertisement_chain():
        """在系统里真造一条 BLE 广播对象（含一段苹果厂商数据和一段别家的），
        走一遍 ble_apple_data 用到的全部 WinRT 调用。"""
        P = ctypes.POINTER
        vp = ctypes.c_void_p
        assert real._winrt() is not None
        real._winrt().RoInitialize(1)

        def make_buffer(data):
            fac = real._factory("Windows.Storage.Streams.Buffer",
                                "{71AF914D-C10F-484B-BC50-14BC623B3A27}")
            assert fac, "IBufferFactory"
            buf = vp()
            assert real._vt(fac, 6, ctypes.c_uint32, P(vp))(
                len(data), ctypes.byref(buf)) >= 0 and buf.value
            real._release(fac)
            acc = real._qi(buf.value, real.IID_BUFFER_BYTES)
            p = vp()
            real._vt(acc, 3, P(vp))(ctypes.byref(p))
            ctypes.memmove(p.value, data, len(data))
            real._release(acc)
            assert real._vt(buf.value, 8, ctypes.c_uint32)(len(data)) >= 0
            return buf.value

        def make_mfr(company, data):
            m = real._activate("Windows.Devices.Bluetooth.Advertisement."
                               "BluetoothLEManufacturerData", real.IID_BLE_MFR)
            assert m, "BluetoothLEManufacturerData"
            assert real._vt(m, 7, ctypes.c_uint16)(company) >= 0
            buf = make_buffer(data)
            assert real._vt(m, 9, vp)(buf) >= 0
            real._release(buf)
            cid = ctypes.c_uint16()
            real._vt(m, 6, P(ctypes.c_uint16))(ctypes.byref(cid))
            assert cid.value == company, cid.value
            return m

        adv = real._activate("Windows.Devices.Bluetooth.Advertisement."
                             "BluetoothLEAdvertisement", real.IID_BLE_ADV)
        assert adv, "BluetoothLEAdvertisement"
        vec = vp()
        assert real._vt(adv, 11, P(vp))(ctypes.byref(vec)) >= 0 and vec.value
        payload = ap_adv()
        for company, data in ((0x0006, b"\x01\x09\x20"), (0x004C, payload)):
            m = make_mfr(company, data)
            assert real._vt(vec.value, 13, vp)(m) >= 0       # IVector.Append
            real._release(m)
        real._release(vec.value)
        got = real.ble_apple_data(adv)
        real._release(adv)
        assert got == [payload], got
        assert real.parse_airpods(got[0])["left"] == 80

    @case
    def winrt_watcher_vtable():
        """广播监听器：读状态、切换扫描模式、挂上/摘下回调、启动/停止都不出错。
        CI 机器没有蓝牙，启动会失败或很快变成中断，这里只检查调用本身。"""
        P = ctypes.POINTER
        w = real._activate(real.BLE_WATCHER_CLASS, real.IID_BLE_WATCHER)
        assert w, "BluetoothLEAdvertisementWatcher"
        st = ctypes.c_int(-1)
        assert real._vt(w, 10, P(ctypes.c_int))(ctypes.byref(st)) >= 0
        assert st.value == 0, st.value                 # Created
        mode = ctypes.c_int(-1)
        real._vt(w, 11, P(ctypes.c_int))(ctypes.byref(mode))
        assert mode.value == 0, mode.value             # 默认被动扫描
        assert real._vt(w, 12, ctypes.c_int)(1) >= 0
        real._vt(w, 11, P(ctypes.c_int))(ctypes.byref(mode))
        assert mode.value == 1, mode.value
        real._vt(w, 12, ctypes.c_int)(0)
        d = real._Delegate(real.IID_BLE_RECEIVED, lambda a: None)
        tok = ctypes.c_int64()
        hr = real._vt(w, 19, ctypes.c_void_p, P(ctypes.c_int64))(
            d.ptr, ctypes.byref(tok))
        assert hr >= 0, hex(hr & 0xFFFFFFFF)
        hr = real._vt(w, 17)()
        real._vt(w, 10, P(ctypes.c_int))(ctypes.byref(st))
        print(f"    Start: hr={hr & 0xFFFFFFFF:#x}, 状态={st.value}")
        real._vt(w, 18)()
        assert real._vt(w, 20, ctypes.c_int64)(tok) >= 0
        real._release(w)

print(f"\n{len(passed)} bluetooth tests passed")
