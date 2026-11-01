"""CPU / GPU 温度的测试：传感器挑选、来源优先级、显示；Windows 上真调系统接口。

用法：python ci/test_temps.py my_devices.py
"""
import ctypes
import importlib.util
import json
import os
import subprocess
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


def load(name):
    spec = importlib.util.spec_from_file_location(name, sys.argv[1])
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


md = load("my_devices")
passed = []


def case(fn):
    fn()
    passed.append(fn.__name__)
    print("ok ", fn.__name__)
    return fn


@case
def same_gpu_listed_twice_is_merged():
    """同一块显卡被枚举两次（PCI 地址相同）只显示一个；标签用厂商名。"""
    nv = {"name": "NVIDIA GeForce RTX 4070", "pci": (1, 0, 0), "value": 52.0}
    dup = dict(nv)
    igpu = {"name": "Intel(R) UHD Graphics 770", "pci": (0, 2, 0),
            "value": 45.0}
    assert md.dedupe_gpus([nv, dup]) == [nv]
    assert md.dedupe_gpus([nv, dup, igpu]) == [nv, igpu]
    a = {"name": "GPU", "pci": None, "value": 60.0}
    assert md.dedupe_gpus([a, dict(a)]) == [a]                # 没地址：名称 + 温度
    b = dict(a, value=61.0)
    assert len(md.dedupe_gpus([a, b])) == 2                   # 温度不同：两块卡
    assert md.gpu_label(nv["name"], "GPU1") == "NVIDIA"
    assert md.gpu_label(igpu["name"], "GPU2") == "Intel"
    assert md.gpu_label("AMD Radeon RX 7800 XT", "GPU1") == "AMD"
    assert md.gpu_label("Some Adapter", "GPU2") == "GPU2"


@case
def pick_intel_package_and_nvidia_core():
    cpu, gpus = md._pick_lhm([
        {"Name": "CPU Core #1", "Parent": "/intelcpu/0", "Value": 61.0},
        {"Name": "CPU Package", "Parent": "/intelcpu/0", "Value": 58.0},
        {"Name": "GPU Core", "Parent": "/gpu-nvidia/0", "Value": 47.0},
        {"Name": "GPU Hot Spot", "Parent": "/gpu-nvidia/0", "Value": 55.0},
        {"Name": "Temperature", "Parent": "/nvme/0", "Value": 40.0},
        {"Name": "CPU Package", "Parent": "/intelcpu/0", "Value": None},
    ])
    assert cpu == 58.0, cpu
    assert gpus == [{"name": "/gpu-nvidia/0", "value": 47.0}], gpus


@case
def pick_amd_tctl_or_hottest():
    cpu, _ = md._pick_lhm([
        {"Name": "Core (Tctl/Tdie)", "Parent": "/amdcpu/0", "Value": 66.5},
        {"Name": "CCD1 (Tdie)", "Parent": "/amdcpu/0", "Value": 60.0}])
    assert cpu == 66.5, cpu
    cpu, _ = md._pick_lhm([
        {"Name": "CCD1", "Parent": "/amdcpu/0", "Value": 50.0},
        {"Name": "CCD2", "Parent": "/amdcpu/0", "Value": 57.0}])
    assert cpu == 57.0, cpu
    assert md._pick_lhm([]) == (None, [])


@case
def source_priority():
    """CPU：LHM 准确值优先，否则热区近似值；GPU：D3DKMT 优先，否则 LHM。"""
    real = (md._lhm_sensors, md.gpu_temperatures, md._cpu_thermal_zone)
    try:
        md._lhm_sensors = lambda: [
            {"Name": "CPU Package", "Parent": "/intelcpu/0", "Value": 58},
            {"Name": "GPU Core", "Parent": "/gpu-amd/0", "Value": 50}]
        md.gpu_temperatures = lambda: [{"name": "RTX", "value": 62.0}]
        md._cpu_thermal_zone = lambda: 40.0
        t = md.read_temperatures()
        assert t["cpu"] == {"value": 58, "approx": False}, t
        assert t["gpus"] == [{"name": "RTX", "value": 62.0}], t
        md.gpu_temperatures = lambda: []
        md._lhm_sensors = lambda: [
            {"Name": "GPU Core", "Parent": "/gpu-amd/0", "Value": 50}]
        t = md.read_temperatures()
        assert t["cpu"] == {"value": 40.0, "approx": True}, t
        assert t["gpus"] == [{"name": "/gpu-amd/0", "value": 50.0}], t
        md._lhm_sensors = lambda: []
        md._cpu_thermal_zone = lambda: None
        t = md.read_temperatures()
        assert t["cpu"] is None and t["gpus"] == [], t
    finally:
        md._lhm_sensors, md.gpu_temperatures, md._cpu_thermal_zone = real


@case
def display_text_and_colors():
    c = md.fluent_colors(False)
    assert md.temp_rgb(None, c) == c["dim"]
    assert md.temp_rgb(55, c) == c["ok"]
    assert md.temp_rgb(78, c) == c["warn"]
    assert md.temp_rgb(92, c) == c["bad"]
    tt = md.Panel._temp_text
    assert tt({"value": 57.6, "approx": False}, True) == "58°C"
    assert tt({"value": 41.2, "approx": True}, True) == "≈41°C"
    assert tt(None, True) == "—" and tt(None, False) == "读取中"


if sys.platform == "win32":
    real = load("my_devices_real")

    @case
    def struct_layout_matches_windows_sdk():
        sz = ctypes.sizeof
        assert sz(real.D3DKMT_ADAPTER_PERFDATA) == 64
        assert sz(real.D3DKMT_ADAPTERINFO) == 20
        assert sz(real.D3DKMT_QUERYADAPTERINFO) == 24
        assert sz(real.D3DKMT_ADAPTERREGISTRYINFO) == 2080
        assert sz(real.PDH_FMT_COUNTERVALUE_ITEM_W) == 24
        assert sz(real.PROCESSENTRY32W) == 568

    @case
    def real_windows_calls_do_not_fail():
        gpus = real.gpu_temperatures()
        tz = real._cpu_thermal_zone()
        running = real._hw_monitor_running()
        t = real.read_temperatures()
        print(f"    这台机器：GPU {gpus}，热区 {tz}，硬件监控在运行 {running}，"
              f"汇总 {json.dumps(t, ensure_ascii=False)}")

    @case
    def lhm_powershell_query_is_valid():
        """没装 LibreHardwareMonitor 时，查询脚本也应正常返回空数组。"""
        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0",
                          "powershell.exe")
        r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                            real._LHM_PS], capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8", "ignore").strip()
        assert r.returncode == 0, r.stderr.decode("utf-8", "ignore")
        assert json.loads(out) == [], out

print(f"\n{len(passed)} temperature tests passed")
