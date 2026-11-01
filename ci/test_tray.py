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


print(f"\n{len(passed)} tray tests passed")
