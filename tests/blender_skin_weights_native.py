"""Run native Blender 5.2/Maya 2024/Max 2020 .qzskin interoperability."""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
ENV = "QZ_SKIN_MATRIX_OUTPUT"
SAMPLE = dict(name="QzSharedSkin", bones=["Root Bone", "Bend", "Tip"],
              points=[(0, 0, 0), (2, 0, 0), (0, 2, 0), (2, 2, 0)],
              faces=[(0, 1, 2), (2, 1, 3)],
              rows=[{"Root Bone": .123456789, "Bend": .376543211, "Tip": .5},
                    {"Root Bone": .4, "Bend": .8}, {"Bend": .4, "Tip": .6},
                    {"Root Bone": .7, "Tip": .3}])


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exchange(folder, tag):
    return folder / f"qz-skin-transfer-{tag}"


def run():
    """Entry point for the existing isolated Maya runner."""
    from maya import cmds
    import maya.api.OpenMaya as om
    from maya_actions.skin_weight_files import skin_weights_files

    assert cmds.about(batch=True)
    folder, phase = Path(os.environ[ENV]), os.environ["QZ_SKIN_MATRIX_PHASE"]
    cmds.file(new=True, force=True)
    transform = cmds.createNode("transform", name=SAMPLE["name"])
    selection = om.MSelectionList()
    selection.add(transform)
    om.MFnMesh().create([om.MPoint(*point) for point in SAMPLE["points"]], [3, 3],
                        [index for face in SAMPLE["faces"] for index in face], parent=selection.getDependNode(0))
    names = {name: cmds.createNode("joint", name=name.replace(" ", "FBXASC032")) for name in SAMPLE["bones"]}
    skin = cmds.skinCluster(list(names.values()), transform, toSelectedBones=True)[0]
    cmds.setAttr(skin + ".normalizeWeights", 0)
    for vertex, expected in enumerate(SAMPLE["rows"]):
        row = expected if phase == "export" else {SAMPLE["bones"][0]: 1.}
        cmds.skinPercent(skin, f"{transform}.vtx[{vertex}]", normalize=False,
                         transformValue=[(node, row.get(name, 0.)) for name, node in names.items()])
    cmds.select(transform)

    def capture(directory):
        result = skin_weights_files(op="export", directory=str(directory))
        assert len(result["meshes"]) == 1 and not result["errors"], result
        return result["meshes"][0]

    if phase == "export":
        result = dict(ok=True, mesh=capture(exchange(folder, "maya-export")))
    else:
        targets = {}
        for source in ("max", "blender"):
            directory = exchange(folder, source + "-maya")
            entry = read(directory / "entry.json")
            entry["mesh"] = skin_weights_files(op="context")["meshes"][0]["mesh"]
            imported = skin_weights_files(op="import", directory=str(directory), meshes=[entry])
            assert len(imported["reports"]) == 1 and not imported["errors"], imported
            targets[source] = capture(directory)
        result = dict(ok=True, targets=targets)
    write(folder / f"maya-{phase}.json", result)
    return result


def _blender_host():
    import bmesh
    import bpy
    from blender_bridge import server

    assert bpy.app.version[:2] == (5, 2), bpy.app.version_string
    bpy.context.preferences.view.show_splash = False
    mesh = None

    def setup(kind="source"):
        nonlocal mesh
        if bpy.context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        layer = bpy.context.view_layer
        collection = bpy.context.scene.collection
        bones = SAMPLE["bones"] if kind != "encoded" else ["Space Bone", "Dot.Bone", "中文骨"]
        if kind == "ambiguous_name":
            bones = [*SAMPLE["bones"], "RootFBXASC032Bone"]
        elif kind == "missing_bone":
            bones = SAMPLE["bones"][:-1]
        rig_data = bpy.data.armatures.new("MatrixArmature")
        rig = bpy.data.objects.new("MatrixRig", rig_data)
        collection.objects.link(rig)
        layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        for index, name in enumerate([*bones, "UnusedDeform"]):
            bone = rig_data.edit_bones.new(name)
            bone.head = (index, 0, 0)
            bone.tail = (index, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
        rig.select_set(False)
        if kind == "ambiguous_rig":
            duplicate = rig.copy()
            collection.objects.link(duplicate)
        points, faces = SAMPLE["points"], SAMPLE["faces"]
        if kind == "closest":
            points = [*points, (.5, .5, 0)]
            faces = [(0, 1, 4), (1, 2, 4), (2, 0, 4), (2, 1, 3)]
        if kind == "reordered":
            points = [points[1], points[0], *points[2:]]
            faces = [tuple({0: 1, 1: 0}.get(index, index) for index in face) for face in faces]
        data = bpy.data.meshes.new(SAMPLE["name"])
        data.from_pydata(points, [], faces)
        mesh = bpy.data.objects.new(SAMPLE["name"], data)
        collection.objects.link(mesh)
        if kind not in ("bind", "ambiguous_rig"):
            modifier = mesh.modifiers.new("Skin", "ARMATURE")
            modifier.object = rig
            modifier.use_deform_preserve_volume = kind == "preserve_volume"
        mesh.vertex_groups.new(name="Unrelated").add([0], .271828, "REPLACE")
        if kind == "source":
            groups = {name: mesh.vertex_groups.new(name=name) for name in SAMPLE["bones"]}
            for vertex, row in enumerate(SAMPLE["rows"]):
                for name, value in row.items():
                    groups[name].add([vertex], value, "REPLACE")
        else:
            mesh.vertex_groups.new(name="UnusedDeform").add(list(range(len(points))), .22, "REPLACE")
        mesh.select_set(True)
        layer.objects.active = mesh
        layer.update()
        return state()

    def state():
        selected = []
        if mesh.mode == "EDIT":
            selected = [vertex.index for vertex in bmesh.from_edit_mesh(mesh.data).verts if vertex.select]
            mesh.update_from_editmode()
        rows = [{mesh.vertex_groups[item.group].name: item.weight for item in vertex.groups}
                for vertex in mesh.data.vertices]
        return dict(rows=rows, selected=selected, mode=mesh.mode,
                    modifiers=[modifier.type for modifier in mesh.modifiers])

    def finish():
        def quit_blender():
            bpy.ops.wm.quit_blender()
            return None

        bpy.app.timers.register(quit_blender, first_interval=1)
        return dict(scheduled=True)

    def initialize():
        setup()
        server.run(port=int(os.environ["QZ_SKIN_MATRIX_PORT"]))
        server.ACTIONS.update(test_skin_setup=setup, test_skin_state=state, test_skin_finish=finish)
        return None

    bpy.app.timers.register(initialize)


@contextmanager
def _launch_blender(executable, folder):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    scratch = folder / "blender-temp"
    scratch.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("BLENDER_USER_", "PYTHON"))}
    env.update(BLENDER_USER_RESOURCES=str(folder / "blender-profile"), TEMP=str(scratch), TMP=str(scratch),
               PYTHONDONTWRITEBYTECODE="1", QZ_SKIN_MATRIX_PORT=str(port))
    command = [str(executable), "--factory-startup", "--offline-mode", "--no-window-focus",
               "--python-exit-code", "1", "--python", str(HERE)]
    with (folder / "blender.log").open("wb") as stream:
        process = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            yield port, process
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)


def _launch_maya(folder, phase):
    env = dict(os.environ, **{ENV: str(folder), "QZ_SKIN_MATRIX_PHASE": phase})
    command = [sys.executable, "-B", str(HERE.with_name("run_maya_native.py")), str(HERE)]
    with (folder / f"maya-{phase}.log").open("wb") as stream:
        subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=75)
    return read(folder / f"maya-{phase}.json")


def _launch_max(executable, folder, phase):
    native = folder / ("max-" + phase)
    native.mkdir()
    directories = ["[Directories]"]
    for key, name in (("Additional Macros", "macros"), ("Additional Scripts", "scripts"),
                      ("Additional Startup Scripts", "startup"), ("MaxData", "appdata"),
                      ("PlugCFG", "plugcfg"), ("PlugCFG_ln", "plugcfg_ln"), ("Additional Icons", "icons")):
        (native / name).mkdir()
        directories.append(f"{key}={native / name}")
    (native / "settings").mkdir()
    config = native / "3dsmax.ini"
    config.write_text("\n".join(directories), encoding="ascii")
    plugins = native / "plugins.ini"
    plugins.write_text(f"[Directories]\nStandard MAX plug-ins={executable.parent / 'stdplugs'}\n", encoding="ascii")
    fixture = HERE.with_name("blender_skin_weights_max_fixture.py").as_posix()
    wrapper = native / "run.py"
    wrapper.write_text(f"import runpy\nrunpy.run_path({fixture!r}, run_name='__main__')\n", encoding="ascii")
    script = native / "run.ms"
    script.write_text(f'python.ExecuteFile @"{wrapper.as_posix()}"\nquitMAX quiet:true exitCode:0\n', encoding="ascii")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "QT_", "PYSIDE", "SHIBOKEN", "ADSK_3DSMAX_"))}
    env.update({ENV: str(folder), "QZ_SKIN_MATRIX_PHASE": phase, "ADSK_3DSMAX_APPDATA_DIR": str(native / "appdata"),
                "ADSK_3DSMAX_USERSETTINGS_DIR": str(native / "settings")})
    command = [str(executable), str(script), "-i", str(config), "-p", str(plugins), "-dm", "on",
               "-log", str(native / "system.log"), "-listenerlog", str(native / "listener.log")]
    with (native / "console.log").open("wb") as stream:
        completed = subprocess.run(command, cwd=executable.parent, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW, timeout=90)
    result = read(folder / f"max-{phase}.json")
    assert result["ok"], result
    return dict(result, returncode=completed.returncode)


def _read_rows(directory, entry):
    import numpy as np

    prefix = directory / f'mesh-{entry["index"]}'
    offsets = np.fromfile(prefix.with_suffix(".offsets"), dtype="<u4")
    indices = np.fromfile(prefix.with_suffix(".cols"), dtype="<u4")
    weights = np.fromfile(prefix.with_suffix(".values"), dtype="<f8")
    names = {bone["index"]: bone["name"].replace("FBXASC032", " ") for bone in entry["bones"]}
    return [{names[indices[index]]: float(weights[index]) for index in range(first, last)}
            for first, last in zip(offsets, offsets[1:])]


def _compare(actual, expected):
    assert len(actual) == len(expected), (actual, expected)
    for index, (left, right) in enumerate(zip(actual, expected)):
        assert left.keys() == right.keys(), (index, left, right)
        assert all(math.isclose(left[bone], right[bone], rel_tol=0, abs_tol=1e-7)
                   for bone in right), (index, left, right)
    return max(abs(left[bone] - right[bone])
               for left, right in zip(actual, expected) for bone in right)


def _rewrite(source, destination, update):
    import zipfile

    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["manifest.json"])
    update(metadata, entries)
    entries["manifest.json"] = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(destination, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)


def exercise(client, process, folder, max_executable):
    from ui import max_skin_weights, skin_weight_files
    from ui.skin_transfer import prepare_transfer
    from blender_actions.skin_transfer_maya import _fbx_name

    run_operation = runpy.run_path(str(ROOT / "plugins/blender/skin_weights/operations.py"))["run_operation"]
    archive_name = skin_weight_files.cache_filename(SAMPLE["name"])
    root = folder / "blender-cache"
    cache = root / "temp" / "skin_weights"
    cache_file = cache / archive_name
    deadline = time.monotonic() + 20
    while client.probe()["state"] != "ready":
        assert process.poll() is None and time.monotonic() < deadline, "Blender bridge did not start"
        time.sleep(.05)
    blender_version = client.last_ping_data["version"]

    def call(action, **params):
        result = client.call(action, timeout=5, **params)
        assert result.get("ok"), result
        return result["data"]

    def op(name, **params):
        result = run_operation(client, name, **params)
        assert result["ok"], result
        return result["data"]

    def success(name, **params):
        result = op(name, **params)
        assert result["count"] == 1 and not result["errors"], result
        return result

    def blender_rows(names=SAMPLE["bones"]):
        state = call("test_skin_state")
        assert math.isclose(state["rows"][0]["Unrelated"], .271828, abs_tol=1e-7)
        assert all("UnusedDeform" not in row for row in state["rows"]), state
        return [{bone: value for bone, value in row.items() if bone in names} for row in state["rows"]]

    assert call("bridge_connect", client_id="skin-matrix-fixture")["connected"]
    client.connected = True
    archives, source_rows, matrix = {}, {}, {}
    with patch("paths.project_dir", return_value=str(root)):
        success("export_file", threshold=1)
        for source in ("blender", "maya", "max"):
            directory = exchange(folder, source + "-export")
            directory.mkdir()
            destination = folder / (source + "-files")
            destination.mkdir()
            if source == "blender":
                archives[source] = Path(shutil.copyfile(cache_file, destination / archive_name))
            else:
                native = _launch_maya(folder, "export") if source == "maya" else _launch_max(max_executable, folder, "export")
                if source == "max":
                    matrix["max_export_exit"] = native["returncode"]
                entry = native["mesh"]
                for_archive, errors = prepare_transfer(directory, [entry], {}, decode_names=False)
                assert len(for_archive) == 1 and not errors, errors
                writer = skin_weight_files.write_archive if source == "maya" else max_skin_weights._write_archive
                archives[source] = writer(directory, destination, for_archive[0], entry)
            target = dict(index=0, mesh=SAMPLE["name"], name=SAMPLE["name"], vertices=len(SAMPLE["points"]))
            payload = skin_weight_files.read_archive(archives[source], directory, target, "closest_point")
            source_rows[source] = _read_rows(directory, payload)
            _compare(source_rows[source], SAMPLE["rows"])

        print("Exports from all three native hosts verified", flush=True)
        for source in ("maya", "max"):
            shutil.copyfile(archives[source], cache_file)
            initial = call("test_skin_setup", kind="bind" if source == "maya" else "target")
            assert any("UnusedDeform" in row for row in initial["rows"])
            success("import_file", threshold=1)
            assert call("test_skin_state")["modifiers"] == ["ARMATURE"]
            matrix[f"{source}_to_blender"] = _compare(blender_rows(), source_rows[source])

        shutil.copyfile(archives["maya"], cache_file)
        for kind, error in (("reordered", "同索引"), ("ambiguous_rig", "多个骨架"),
                            ("ambiguous_name", "无法唯一匹配"), ("missing_bone", "缺少文件骨骼")):
            before = call("test_skin_setup", kind=kind)
            result = op("import_file")
            assert result["count"] == 0 and error in result["errors"][0]["error"], result
            assert call("test_skin_state") == before
        _rewrite(archives["maya"], cache_file, lambda manifest, _: manifest.update(skinning_method=2))
        assert "DQ" in op("import_file")["errors"][0]["error"]
        assert call("test_skin_state") == before
        _rewrite(archives["maya"], cache_file, lambda manifest, _: manifest.update(version=2))
        assert "没有几何证明" in op("import_file")["errors"][0]["error"]
        assert call("test_skin_state") == before
        call("test_skin_setup", kind="preserve_volume")
        archive_before = cache_file.read_bytes()
        assert "保留体积" in op("export_file")["errors"][0]["error"]
        assert cache_file.read_bytes() == archive_before

        shutil.copyfile(archives["max"], cache_file)
        call("test_skin_setup", kind="closest")
        result = success("import_file", threshold=1, transfer_mode="closest_point")
        assert result["reports"][0]["direct_vertices"] == 4 and result["selected_vertices"] == 1, result
        assert call("test_skin_state")["selected"] == [4]
        faces = source_rows["max"]
        point = {bone: sum(faces[i].get(bone, 0.) * weight for i, weight in enumerate((.5, .25, .25)))
                 for bone in SAMPLE["bones"]}
        _compare(blender_rows(), [*faces, point])

        def encode(manifest, contents):
            names = {"RootFBXASC032Bone": "Space Bone", "Root Bone": "Space Bone", "Bend": "Dot.Bone", "Tip": "中文骨"}
            for bone in manifest["bones"]:
                bone["name"] = _fbx_name(names[bone["name"]])

        _rewrite(archives["maya"], cache_file, encode)
        call("test_skin_setup", kind="encoded")
        success("import_file")
        names = dict(zip(SAMPLE["bones"], ["Space Bone", "Dot.Bone", "中文骨"]))
        _compare(blender_rows(names.values()), [{names[bone]: value for bone, value in row.items()}
                                                for row in source_rows["maya"]])

        for mode, expected in ((0, {"Bend": .430, "Tip": .570}), (1, {"Bend": .438, "Tip": .562})):
            call("test_skin_setup")
            assert op("check", threshold=2)["max_influences"] == 3
            assert op("select_excess", threshold=2)["count"] == 1
            assert call("test_skin_state")["selected"] == [0]
            repaired = success("limit", threshold=2, mode=mode)
            assert repaired["max_influences"] == 2, repaired
            _compare(blender_rows(), [expected, *source_rows["blender"][1:]])

    print("Blender native modes, guards and repair verified", flush=True)
    for target in ("maya", "max"):
        for source in ("blender", "max" if target == "maya" else "maya"):
            directory = exchange(folder, source + "-" + target)
            directory.mkdir()
            model = dict(index=0, mesh=SAMPLE["name"], name=SAMPLE["name"], vertices=len(SAMPLE["points"]), node_handle=0)
            reader = skin_weight_files.read_archive if target == "maya" else max_skin_weights._read_archive
            entry = reader(archives[source], directory, model)
            write(directory / "entry.json", entry)
        native = _launch_maya(folder, "import") if target == "maya" else _launch_max(max_executable, folder, "import")
        if target == "max":
            matrix["max_import_exit"] = native["returncode"]
        for source, entry in native["targets"].items():
            directory = exchange(folder, source + "-" + target)
            ready, errors = prepare_transfer(directory, [entry], {}, decode_names=False)
            assert len(ready) == 1 and not errors, errors
            matrix[f"{source}_to_{target}"] = _compare(_read_rows(directory, ready[0]), source_rows[source])
    assert call("test_skin_finish")["scheduled"]
    assert call("bridge_disconnect")["stopped"]
    assert process.wait(timeout=10) == 0
    return dict(ok=True, blender=blender_version, tolerance=1e-7, **matrix)


def main():
    from dcc_bridge import DccClient

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--max", required=True, type=Path)
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="blender-skin-matrix-", dir=ROOT / "build"))
    write(folder / "sample.json", SAMPLE)
    print(f"Native output: {folder}", flush=True)
    with _launch_blender(args.blender.resolve(strict=True), folder) as (port, process):
        result = exercise(DccClient.for_dcc("blender", port=port), process, folder, args.max.resolve(strict=True))
    write(folder / "matrix.json", result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    if "QZ_SKIN_MATRIX_PORT" in os.environ:
        _blender_host()
    else:
        main()
