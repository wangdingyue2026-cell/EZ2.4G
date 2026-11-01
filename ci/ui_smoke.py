"""CI 冒烟测试：在 Windows 上真实渲染各个界面并截图，任何异常都让构建失败。

用法：python ci/ui_smoke.py my_devices.py <截图目录>
"""
import importlib.util
import os
import subprocess
import sys
import threading
import time
import types

from PIL import ImageGrab

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
spec = importlib.util.spec_from_file_location("my_devices", src)
md = importlib.util.module_from_spec(spec)
sys.modules["my_devices"] = md
spec.loader.exec_module(md)

md.user32.SetProcessDPIAware()
md.user32.GetDpiForSystem.restype = md.wt.UINT
md.UI_SCALE = max(1.0, md.user32.GetDpiForSystem() / 96.0)

errors = []


def reading(**kw):
    vid, pid = kw.pop("vid", 0), kw.pop("pid", 0)
    r = md.Reading(**kw)
    r.vid, r.pid = vid, pid
    return r


SNAP = {
    "a": reading(ok=True, level=100, name="ATK mini A9 pro 鼠标",
                 status="discharging", vid=0x373B, pid=0x1278),
    "b": reading(ok=True, level=70, name="MCHOSE V9 Turbo 耳机",
                 status="charging", vid=0x3837, pid=0x600D),
    "c": reading(ok=True, level=18, name="Keyboard K2", stale=True,
                 detail="上次更新 20:45 · 设备休眠", vid=1, pid=2),
    "d": reading(ok=False, name="未知接收器 1A2B:3C4D",
                 status="unknown_proto", vid=0x1A2B, pid=0x3C4D),
    "bt_001a7dda7113": reading(ok=True, level=45, name="WH-1000XM5",
                               status="bluetooth", detail="已连接",
                               kind="bluetooth"),
}


TEMPS = {
    False: {"cpu": {"value": 58.0, "approx": False},
            "gpus": [{"name": "NVIDIA", "value": 76.0},
                     {"name": "Intel", "value": 49.0}], "ts": 0},
    True: {"cpu": {"value": 41.0, "approx": True}, "gpus": [], "ts": 0},
}


def make_app(light, snap):
    return types.SimpleNamespace(
        _temps=TEMPS[light],
        theme_light=light, panel_only=False, snapshot=lambda: dict(snap),
        _refreshing=False, _lock=threading.Lock(), _readings=snap,
        refresh=lambda: None, remove_device=lambda k: None,
        rename_device=lambda k, t: None, quit=lambda: None, interval=60,
        add_device=lambda *a: None, _add_win=None)


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def grab(win, name, pad=24):
    win.update_idletasks()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    ImageGrab.grab(bbox=(x - pad, y - pad, x + w + pad, y + h + pad),
                   all_screens=True).save(os.path.join(out, name + ".png"))


def record(root, bbox, name, seconds):
    """连续截屏存成 GIF，用来检查动效。"""
    frames, stamps = [], []
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        frames.append(ImageGrab.grab(bbox=bbox, all_screens=True)
                      .convert("P", palette=1, colors=255))
        stamps.append(time.time())
    if len(frames) > 1:
        gaps = [max(20, int((b - a) * 1000))
                for a, b in zip(stamps, stamps[1:])] + [800]
        frames[0].save(os.path.join(out, name + ".gif"), save_all=True,
                       append_images=frames[1:], duration=gaps, loop=0)


def report(exc, val, tb):
    import traceback
    errors.append("".join(traceback.format_exception(exc, val, tb)))


for light in (False, True):
    tag = "light" if light else "dark"
    for label, snap in (("devices", SNAP), ("empty", {})):
        app = make_app(light, dict(snap))
        p = md.Panel(app)
        app.panel = p
        p.root.report_callback_exception = report
        p._check_dismiss = lambda: None  # 无人值守，别让失焦把面板收起
        p.show()  # show() 同步算好最终位置，动画随后才跑
        x, y = p._anchor()
        box = (x - 40, y - 40, x + p.W + 40, y + p.H + 40)
        if label == "devices" and not light:
            record(p.root, box, "anim_open_dark", 1.4)
        pump(p.root, 1.5)
        grab(p.root, f"panel_{label}_{tag}")
        if label == "devices":
            cy = p._list[0] + p.CARD_H / 2  # 第一张卡片中间
            ev = types.SimpleNamespace(
                x=int(200 * p.S), y=int(cy * p.S),
                x_root=p.root.winfo_rootx() + int(200 * p.S),
                y_root=p.root.winfo_rooty() + int(cy * p.S))
            p._on_right_click(ev)
            pump(p.root, 0.3)
            if not p._menu:
                errors.append("right-click on the first card opened no menu")
            else:
                grab(p._menu.root, f"menu_{tag}")
                p._close_menu()
            p._toggle_edit()
            pump(p.root, 0.3)
            if not p._btns.get("btn_del_a"):
                errors.append("edit mode shows no delete button on card a")
            grab(p.root, f"edit_{tag}")
            p._toggle_edit()
            pump(p.root, 0.2)
            p._edit_name_at("a")
            pump(p.root, 0.2)
            grab(p.root, f"rename_{tag}")
            p._commit_name(False)
            if not light:
                p._hover_card("a", True)
                p._toggle_startup()
                record(p.root, box, "anim_hover_toggle_dark", 0.7)
                p._hover_card("a", False)
                pump(p.root, 0.3)
            p.open_help("d")
            pump(p.root, 0.9)
            grab(p._help_win.root, f"help_{tag}")
            p._close_help()
            app._add_win = md.AddDeviceWindow(app, 0x373B, 0x1278,
                                              "ATK mini A9 pro", 85)
            pump(p.root, 1.2)
            grab(app._add_win.root, f"add_{tag}")
            app._add_win._done(False)
        if label == "devices" and light:
            p.show()
            pump(p.root, 1.5)
            p.hide()
            record(p.root, box, "anim_close_light", 0.4)
        p.close()

# 真实入口：托盘模式与 --panel 模式各跑几秒，stderr 里不能有 Traceback
for args in (["--panel"], []):
    proc = subprocess.Popen([sys.executable, src] + args,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    time.sleep(5)
    if args:
        ImageGrab.grab(all_screens=True).save(
            os.path.join(out, "real_panel_only.png"))
    proc.kill()
    err = proc.communicate()[1]
    if "Traceback" in err:
        errors.append(f"{args}:\n{err}")

if errors:
    print("\n\n".join(errors))
    sys.exit(1)
print("UI smoke test passed; screenshots in", out)
