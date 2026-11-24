"""Exercise the bridge in an isolated Blender 5.2 GUI process on a random port."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["A Cube", "B 立方体"]


def run_host(output):
    import threading
    import bpy

    if bpy.app.version[:2] != (5, 2):
        raise RuntimeError(f"Expected Blender 5.2, got {bpy.app.version_string}")
    bpy.context.preferences.view.show_splash = False
    startup = (output / "startup.txt").read_text(encoding="utf-8")
    command = output / "command"
    marker = output / "marker"

    def start_bridge():
        window = bpy.context.window_manager.windows[0]
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        area.type = "CONSOLE"
        try:
            region = next(region for region in area.regions if region.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.console.language(language="python") == {"FINISHED"}
                assert bpy.ops.console.insert(text=startup) == {"FINISHED"}
                assert bpy.ops.console.execute() == {"FINISHED"}
        finally:
            area.type = "VIEW_3D"

    def set_selection(names):
        layer = bpy.context.window_manager.windows[0].view_layer
        for obj in layer.objects:
            obj.select_set(obj.name in names, view_layer=layer)

    def slow_selection():
        from blender_actions.read_selection import read_selection

        assert threading.current_thread() is threading.main_thread()
        marker.write_text("busy", encoding="ascii")
        time.sleep(1.2)
        return read_selection()

    def control():
        if not command.exists():
            return .05
        operation = command.read_text(encoding="ascii")
        command.unlink()
        if operation == "stall":
            marker.write_text("stall", encoding="ascii")
            time.sleep(1.2)
        elif operation == "clear":
            set_selection([])
            marker.write_text("cleared", encoding="ascii")
        elif operation == "restart":
            set_selection(NAMES)
            start_bridge()
        elif operation == "quit":
            bpy.ops.wm.quit_blender()
            return None
        return .05

    def initialize():
        bpy.data.objects["Cube"].name = NAMES[0]
        bpy.data.objects["Camera"].name = NAMES[1]
        set_selection(NAMES)
        start_bridge()
        from blender_bridge import server
        server.ACTIONS["test_slow_selection"] = slow_selection
        bpy.app.timers.register(control)
        return None

    phase = os.environ.get("QZ_BLENDER_NATIVE_AUTO_SETUP")
    if phase:
        def observe_startup():
            (output / f"{phase}.ready").touch()
            bpy.app.timers.register(control)
            return None

        bpy.app.timers.register(observe_startup, first_interval=1)
    else:
        bpy.app.timers.register(initialize)


def exercise(client, process, output):
    from ui import blender_setup

    command = output / "command"
    marker = output / "marker"

    def wait_for(predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and process.poll() is None:
            if predicate():
                return
            time.sleep(.025)
        raise AssertionError(message)

    def wait_for_state(state, timeout=5):
        wait_for(lambda: client.probe()["state"] == state, f"Expected {state}", timeout)

    def move_host(operation):
        pending = command.with_suffix(".new")
        pending.write_text(operation, encoding="ascii")
        pending.replace(command)

    def call(action, **params):
        result = client.call(action, **params)
        assert result.get("ok"), result
        return result["data"]

    selected = {"count": 2, "names": NAMES}
    wait_for_state("ready", timeout=30)
    version = client.last_ping_data["version"]
    assert version.startswith("5.2."), version
    assert client.last_ping_data["bridge_protocol"] == 3
    assert call("bridge_connect", client_id="native-blender")["connected"]
    assert call("read_selection") == selected

    move_host("stall")
    wait_for(marker.exists, "Main-thread stall did not start")
    wait_for_state("waiting", timeout=1)
    assert client.last_ping_data["version"] == version
    assert not client.call("bridge_disconnect")["ok"]
    assert not client.call("read_selection")["ok"]
    wait_for_state("ready")
    marker.unlink()

    with ThreadPoolExecutor(1) as worker:
        selection = worker.submit(call, "test_slow_selection")
        wait_for(marker.exists, "Main-thread action did not start")
        assert client.probe()["state"] == "busy"
        assert not client.call("bridge_disconnect")["ok"]
        assert not client.call("read_selection")["ok"]
        assert selection.result() == selected
    marker.unlink()

    move_host("clear")
    wait_for(marker.exists, "Selection did not clear")
    assert call("read_selection") == {"count": 0, "names": []}
    assert call("bridge_disconnect")["stopped"]
    assert client.probe()["state"] == "offline"

    move_host("restart")
    wait_for_state("ready")
    assert call("bridge_connect", client_id="native-blender")["connected"]
    assert call("read_selection") == selected
    assert {"read_selection", "reload_actions", "user_script_blender"} <= set(call("reload_actions")["actions"])
    assert call("user_script_blender", op="info")["languages"] == ["python"]
    exercise_user_scripts(client, output, wait_for)
    startup_dir = Path(call("get_startup_dir")["path"]).resolve()
    assert startup_dir.is_relative_to((output / "profile").resolve()), startup_dir
    blender_setup.write_script(startup_dir, client.port)
    assert call("bridge_disconnect")["stopped"]
    assert client.probe()["state"] == "offline"

    move_host("quit")
    assert process.wait(timeout=10) == 0
    print(f"PASS Blender {version}: console startup, selection, waiting, busy, disconnect, same-port restart")


def exercise_user_scripts(client, output, wait_for):
    from ui import user_scripts

    client.connected = True
    source = output / "用户工具"
    source.mkdir()
    (source / "资源.txt").write_text("Blender 用户脚本", encoding="utf-8")
    (source / "qz_native_fixture_helper.py").write_text('PREFIX = "测试："\n', encoding="utf-8")
    (source / "main.py").write_text(
        '''
import bpy
from pathlib import Path
import threading
import time
from qz_native_fixture_helper import PREFIX

def main():
    assert threading.current_thread() is threading.main_thread()
    assert bpy.context.window and bpy.context.view_layer
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.object.name = "Qz Native User Script"
    folder = Path(__file__).parent
    (folder / "running").touch()
    print(PREFIX + (folder / "资源.txt").read_text(encoding="utf-8"))
    time.sleep(2.3)
''', encoding="utf-8")
    tool = user_scripts.add_script("文件夹工具", str(source), "main", dcc="blender")
    marker = Path(tool["directory"]) / "running"
    assert not marker.exists()
    with ThreadPoolExecutor(1) as worker:
        result = worker.submit(user_scripts.run_script, client, tool)
        wait_for(marker.exists, "Imported script did not start")
        assert client.probe()["state"] == "busy"
        assert not client.call("bridge_disconnect")["ok"]
        response = result.result()
    assert response.get("ok"), response
    assert response["data"]["elapsed_seconds"] >= 2
    assert "测试：Blender 用户脚本" in response["data"]["output"], response
    assert client.call("read_selection")["data"] == {"count": 1, "names": ["Qz Native User Script"]}

    pasted = user_scripts.add_code("粘贴工具", '''
if __name__ == "__main__":
    print("入口执行")
''', dcc="blender")
    response = user_scripts.run_script(client, pasted)
    assert response.get("ok") and response["data"]["output"].strip() == "入口执行", response
    invalid = client.call("user_script_blender", op="run", filepath=str(source / "main.py"),
                          directory=str(source), entry="main", script_id=tool["id"])
    assert not invalid["ok"], invalid
    print("PASS Blender managed scripts: folder, helper, resources, pasted code, long main-thread run")


@contextmanager
def launch(executable, env, log, *, factory_startup=False):
    print(f"Blender log: {log}", flush=True)
    command = [str(executable), "--offline-mode", "--python-exit-code", "1", "--python", __file__]
    if factory_startup:
        command.insert(1, "--factory-startup")
    with log.open("wb") as stdout:
        process = subprocess.Popen(command, env=env, stdout=stdout, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def exercise_auto_setup(executable, env, output, client):
    from ui import blender_setup

    for phase in ("installed", "uninstalled"):
        phase_env = dict(env, QZ_BLENDER_NATIVE_AUTO_SETUP=phase)
        with launch(executable, phase_env, output / f"{phase}.log") as process:
            deadline = time.monotonic() + 30
            while not (output / f"{phase}.ready").exists():
                if time.monotonic() >= deadline or process.poll() is not None:
                    raise AssertionError(f"Blender {phase} startup did not finish")
                time.sleep(.05)
            if phase == "installed":
                report = client.probe()
                assert report["state"] == "ready", report
                assert client.call("bridge_connect", client_id="native-auto-setup")["ok"]
                directory = Path(client.call("get_startup_dir")["data"]["path"]).resolve()
                assert directory.is_relative_to((output / "profile").resolve()), directory
                assert blender_setup.remove_script(directory) == directory / blender_setup.SCRIPT_NAME
                assert client.probe()["state"] == "ready"
            else:
                assert client.probe()["state"] == "offline"
            (output / "command").write_text("quit", encoding="ascii")
            assert process.wait(timeout=10) == 0
    print("PASS Blender auto setup: installed profile opens the bridge, uninstalled profile leaves it offline")


def main(executable):
    sys.path.insert(0, str(ROOT))
    from dcc_bridge import DccClient
    from ui.blender_setup import snippet

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="blender-5.2-native-", dir=build))
    os.environ["QZ_USER_PLUGINS_DIR"] = str(output / "user_plugins")
    scratch = output / "temp"
    scratch.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (output / "startup.txt").write_text(snippet(port), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BLENDER_USER_", "PYTHON"))}
    env.update(
        QZ_BLENDER_NATIVE_OUTPUT=str(output),
        BLENDER_USER_RESOURCES=str(output / "profile"),
        TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
        PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
    )
    client = DccClient.for_dcc("blender", port=port)
    with launch(executable, env, output / "console.log", factory_startup=True) as process:
        exercise(client, process, output)
    exercise_auto_setup(executable, env, output, client)


if __name__ == "__main__":
    if "QZ_BLENDER_NATIVE_OUTPUT" in os.environ:
        run_host(Path(os.environ["QZ_BLENDER_NATIVE_OUTPUT"]))
    elif len(sys.argv) == 2:
        main(Path(sys.argv[1]).resolve(strict=True))
    else:
        raise SystemExit("Usage: run_blender_native.py <path to Blender 5.2 executable>")
