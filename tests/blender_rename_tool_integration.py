"""Run the batch rename card and actions against an isolated Blender 5.2 GUI."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ENV = "QZ_BLENDER_RENAME_NATIVE_OUTPUT"


def run_host(output):
    import bpy

    assert bpy.app.version[:2] == (5, 2), bpy.app.version_string
    bpy.context.preferences.view.show_splash = False

    def initialize():
        scene = bpy.context.scene
        layer = bpy.context.window_manager.windows[0].view_layer
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        objects = {}

        def add(key, name, *, collection=scene.collection, parent=None, data=None):
            obj = bpy.data.objects.new(name, data)
            collection.objects.link(obj)
            obj.parent = parent
            objects[key] = obj
            return obj

        mesh = bpy.data.meshes.new("Shared mesh data")
        root = add("root", "Root", data=mesh)
        wing = add("wing", "Wing", parent=root)
        outside = bpy.data.collections.new("Excluded")
        scene.collection.children.link(outside)
        middle = add("middle", "Intermediate", collection=outside, parent=wing)
        add("tip", "尖端", parent=middle)
        layer.layer_collection.children[outside.name].exclude = True
        add("asset", "Asset")
        add("occupied", "Asset.001")
        hidden = add("hidden", "Asset.010")
        add("locked", "Asset.011").hide_select = True
        add("candidate", "Candidate")
        add("piece3", "Piece.003")
        add("piece4", "Piece.004")
        add("part1", "PiecePart")
        add("part2", "PartPiece")
        add("isolated", "Isolated.004")
        add("long", "Long")
        add("one", "一")

        library_path = str(output / "linked.blend")
        source = bpy.data.objects.new("Common", None)
        bpy.data.libraries.write(library_path, {source})
        bpy.data.objects.remove(source)
        add("local", "Common")
        with bpy.data.libraries.load(library_path, link=True) as (_, loaded):
            loaded.objects = ["Common"]
        objects["linked"] = loaded.objects[0]
        scene.collection.objects.link(objects["linked"])
        layer.update()
        hidden.hide_set(True, view_layer=layer)

        def fixture(selected=None, quit=False):
            if quit:
                def close():
                    bpy.ops.wm.quit_blender()
                    return None

                bpy.app.timers.register(close, first_interval=1)
            if selected is not None:
                requested = {objects[key] for key in selected}
                for obj in layer.objects:
                    desired = obj in requested
                    if obj.select_get(view_layer=layer) != desired:
                        obj.select_set(desired, view_layer=layer)
                layer.objects.active = objects[selected[0]] if selected else None
            return {"names": {key: obj.name for key, obj in objects.items()},
                    "selected": sorted(key for key, obj in objects.items()
                                       if obj.select_get(view_layer=layer)),
                    "active": next((key for key, obj in objects.items() if obj == layer.objects.active), None),
                    "mesh": mesh.name}

        startup = (output / "startup.txt").read_text(encoding="utf-8")
        exec(compile(startup, "<Qz Blender startup>", "single"), {})
        from blender_bridge import server
        server.ACTIONS["test_rename_fixture"] = fixture
        return None

    bpy.app.timers.register(initialize)


def exercise(client, process):
    deadline = time.monotonic() + 30
    while client.probe()["state"] != "ready":
        assert process.poll() is None and time.monotonic() < deadline, "Blender bridge did not start"
        time.sleep(.05)
    version = client.last_ping_data["version"]
    assert version.startswith("5.2."), version

    def call(action, **params):
        result = client.call(action, timeout=3, connect_timeout=1, **params)
        assert result["ok"], result
        return result["data"]

    def rename(op, **params):
        return call("rename_tool", op=op, **params)

    def select(*keys):
        state = call("test_rename_fixture", selected=keys)
        assert state["selected"] == sorted(keys), state

    assert call("bridge_connect", client_id="rename-native") == {"connected": True}
    select()
    assert rename("prefix", prefix="L_")["warning"] == "当前视图层没有选中对象"
    select("root")
    assert rename("prefix", prefix="L_")["names"] == ["L_Root"]
    assert rename("suffix", suffix="_geo")["names"] == ["L_Root_geo"]
    click_replace_card(client)
    actual = call("test_rename_fixture")
    assert actual["names"]["root"] == "L_树_geo" and actual["names"]["wing"] == "Wing", actual
    assert actual["mesh"] == "Shared mesh data" and actual["selected"] == ["root"], actual
    assert rename("replace", search="missing", replace="unused")["skipped"] == 1

    select("root", "wing")
    result = rename("rename", hierarchy=True, base="部件", prefix="L_", suffix="_geo",
                    use_prefix=True, use_suffix=True)
    expected = ["L_部件01_geo", "L_部件02_geo", "L_部件03_geo"]
    assert result == {"count": 3, "names": expected}, result
    actual = call("test_rename_fixture")
    assert [actual["names"][key] for key in ("tip", "wing", "root")] == expected, actual
    assert actual["names"]["middle"] == "Intermediate" and actual["selected"] == ["root", "wing"], actual
    select("tip")
    assert rename("delete_index", index=2)["names"] == ["L_件01_geo"]
    assert rename("delete_index", index=-1)["names"] == ["L_件01_ge"]
    assert rename("delete_index", index=255)["skipped"] == 1
    assert rename("replace", search="_ge", replace="")["names"] == ["L_件01"]
    select("one")
    assert rename("delete_index", index=0)["skipped"] == 1
    result = rename("replace", search="一", replace="Intermediate")
    assert result["names"] == ["Intermediate.001"] and result["adjusted"] == 1, result

    select("candidate")
    result = rename("replace", search="Candidate", replace="Asset")
    assert result == {"count": 1, "names": ["Asset.002"], "adjusted": 1,
                      "adjustments": [["Asset", "Asset.002"]]}, result
    result = rename("replace", search=".002", replace="")
    assert result["count"] == 0 and result["adjustments"] == [["Asset", "Asset.002"]], result
    state = call("test_rename_fixture")
    assert [state["names"][key] for key in ("asset", "occupied", "candidate")] == ["Asset", "Asset.001", "Asset.002"], state
    assert state["selected"] == ["candidate"], state

    select("long")
    result = rename("replace", search="Long", replace="重" * 100)
    assert result["names"] == ["重" * 85] and result["adjusted"] == 1, result
    select("linked")
    result = rename("prefix", prefix="L_")
    assert result["readonly"] == result["skipped"] == 1 and result["count"] == 0, result

    select("root", "candidate")
    result = rename("check_duplicates")
    assert result["groups"] == 3 and result["count"] == 7 and result["skipped"] == 2, result
    state = call("test_rename_fixture")
    assert state["selected"] == ["asset", "candidate", "linked", "local", "occupied", "piece3", "piece4"], state
    assert state["active"] in state["selected"], state
    assert state["names"]["local"] == state["names"]["linked"] == "Common", state
    assert state["names"]["middle"] == "Intermediate", state
    assert call("read_selection")["count"] == 7

    select("part1", "part2")
    result = rename("replace", search="Piece", replace="")
    assert set(result["names"]) == {"Part", "Part.001"} and result["adjusted"] == 1, result
    assert call("read_selection")["names"] == ["Part", "Part.001"]

    call("test_rename_fixture", quit=True)
    assert "rename_tool" in call("reload_actions")["actions"]
    assert call("bridge_disconnect")["stopped"]
    assert process.wait(timeout=10) == 0
    print(f"PASS Blender {version}: rename card over TCP, hierarchy, Unicode, collisions, linked IDs, duplicate selection, reload")


def click_replace_card(client):
    from types import SimpleNamespace
    from PySide6 import QtCore, QtTest, QtWidgets
    from ui.plugin_loader import scan

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    plugin = next(info for info in scan(str(ROOT / "plugins")) if info.key == ("blender", "rename_tool"))
    messages = []
    log = SimpleNamespace(info=messages.append, warn=messages.append, error=messages.append)
    card = plugin.load_factory()(client, log)
    card.show()
    card.search_edit.setText("Root")
    card.replace_edit.setText("树")
    QtTest.QTest.mouseClick(card.replace_btn, QtCore.Qt.LeftButton)
    deadline = QtCore.QDeadlineTimer(4000)
    while not card.replace_btn.isEnabled() and not deadline.hasExpired():
        QtTest.QTest.qWait(5)
    assert any("L_树_geo" in message for message in messages), messages
    card.close()
    app.processEvents()


def main(executable):
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dcc_bridge import DccClient
    from ui.blender_setup import snippet

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="blender-rename-", dir=build))
    scratch = output / "temp"
    scratch.mkdir()
    os.environ["QZ_USER_PLUGINS_DIR"] = str(output / "user_plugins")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (output / "startup.txt").write_text(snippet(port), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("BLENDER_USER_", "PYTHON"))}
    environment.update({OUTPUT_ENV: str(output), "BLENDER_USER_RESOURCES": str(output / "profile"),
                        "TMP": str(scratch), "TEMP": str(scratch),
                        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
    command = [str(executable), "--factory-startup", "--offline-mode", "--no-window-focus",
               "--python-exit-code", "1", "--python", __file__]
    log = output / "blender.log"
    print(f"Blender log: {log}", flush=True)
    with log.open("wb") as stream:
        process = subprocess.Popen(command, env=environment, stdout=stream, stderr=subprocess.STDOUT)
        try:
            exercise(DccClient.for_dcc("blender", port=port), process)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    if OUTPUT_ENV in os.environ:
        run_host(Path(os.environ[OUTPUT_ENV]))
    elif len(sys.argv) == 2:
        main(Path(sys.argv[1]).resolve(strict=True))
    else:
        raise SystemExit("Usage: blender_rename_tool_integration.py <path to Blender 5.2 executable>")
