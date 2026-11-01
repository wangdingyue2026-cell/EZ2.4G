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
    md._bt_nodes = lambda: [
        {"inst": HFP, "level": 80, "name": "WH-1000XM5", "container": "aa"},
        {"inst": LE, "level": 45, "name": "Surface Pen", "container": "bb"}]
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

print(f"\n{len(passed)} bluetooth tests passed")
