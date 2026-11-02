"""托盘图标内容的测试：只显示最低电量，提示里列出所有设备。

用法：python ci/test_tray.py my_devices.py
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
R = md.Reading

passed = []


def case(fn):
    fn()
    passed.append(fn.__name__)
    print("ok ", fn.__name__)
    return fn


@case
def picks_lowest_and_lists_others():
    snap = {
        "a": R(ok=True, level=100, name="鼠标"),
        "b": R(ok=True, level=18, name="键盘", status="charging"),
        "c": R(ok=True, level=45, name="耳机", kind="bluetooth"),
        "d": R(ok=False, name="未知接收器", status="unknown_proto"),
        "e": R(ok=False, name="没插的", present=False),
    }
    level, charging, tip = md.tray_summary(snap)
    assert level == 18 and charging, (level, charging)
    assert tip.split("\n") == ["2.4G · 最低 18%：键盘", "耳机  45%", "鼠标  100%"], tip


@case
def stale_reading_counts():
    level, _c, tip = md.tray_summary({
        "a": R(ok=True, level=70, name="鼠标"),
        "b": R(ok=True, level=30, name="键盘", stale=True)})
    assert level == 30 and tip.startswith("2.4G · 最低 30%：键盘"), tip


@case
def empty_states_keep_an_icon():
    assert md.tray_summary({}) == (None, False, "2.4G · 没有设备")
    level, _c, tip = md.tray_summary({"d": R(ok=False, name="x",
                                            status="unknown_proto")})
    assert level is None and tip == "2.4G · 暂无电量读数", tip


@case
def tooltip_fits_windows_limit():
    snap = {str(i): R(ok=True, level=50 + i, name="很长的设备名字" * 3)
            for i in range(10)}
    _l, _c, tip = md.tray_summary(snap)
    assert len(tip) <= 127 and tip.startswith("2.4G · 最低 50%"), len(tip)


@case
def low_battery_alert_once_per_stage():
    """20% 以下提醒一次，10% 以下再提醒一次；充电或回到 30% 以上才重新布防。"""
    st = {}
    snap = {"m": R(ok=True, level=50, name="鼠标")}
    assert md.low_alerts(snap, st) == []
    snap["m"].level = 18
    assert [r.name for r in md.low_alerts(snap, st)] == ["鼠标"]
    assert md.low_alerts(snap, st) == []            # 同一档不重复
    snap["m"].level = 22                            # 20~30 之间不重新布防
    assert md.low_alerts(snap, st) == []
    snap["m"].level = 15
    assert md.low_alerts(snap, st) == []
    snap["m"].level = 9
    assert len(md.low_alerts(snap, st)) == 1        # 第二档
    snap["m"].status = "charging"
    assert md.low_alerts(snap, st) == [] and st == {}
    snap["m"].status = "discharging"
    assert len(md.low_alerts(snap, st)) == 1        # 充过电又低了，再提醒


@case
def low_battery_alert_skips_unreliable_and_merges():
    st = {}
    snap = {"a": R(ok=True, level=12, name="键盘", stale=True),
            "b": R(ok=False, name="没读到"),
            "c": R(ok=True, level=5, name="鼠标", present=False),
            "d": R(ok=True, level=8, name="手柄"),
            "e": R(ok=True, level=16, name="耳机", kind="bluetooth")}
    rs = md.low_alerts(snap, st)
    assert [r.name for r in rs] == ["手柄", "耳机"], rs
    text, title = md.low_alert_text(rs)
    assert title == "电量低" and text == "这些设备该充电了：手柄 8%、耳机 16%", text
    text, title = md.low_alert_text(rs[:1])
    assert title == "电量很低" and "手柄" in text and "8%" in text, text
    text, title = md.low_alert_text(rs[1:])
    assert title == "电量低" and "耳机" in text and "16%" in text, text
    assert len(text) < 256


print(f"\n{len(passed)} tray tests passed")
