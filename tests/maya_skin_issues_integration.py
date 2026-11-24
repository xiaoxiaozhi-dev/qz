"""Problem selection and native weight hammer in a disposable Maya batch scene."""
from pathlib import Path
import struct
import tempfile


def run():
    from maya import cmds
    from maya_actions import skin_weights as action
    if not cmds.about(batch=True):
        raise RuntimeError('Requires a separate Maya batch process')
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    meshes, expected, before = [], {}, {}
    for index, broken in enumerate(((6, 8), (12,))):
        transform = cmds.polyPlane(name='qzIssueMesh%d' % index, sx=4, sy=4)[0]
        joints = []
        for number in range(2):
            cmds.select(clear=True)
            joints.append(cmds.joint(name='qzIssueJoint%d_%d' % (index, number), position=(number, 0, 0)))
        skin = cmds.skinCluster(joints, transform, toSelectedBones=True)[0]
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        mesh = action._Mesh(shape)
        settings = mesh.settings()
        mesh.set_settings(settings)
        rows = [.8, .2] * mesh.vertices
        for vertex in broken:
            rows[vertex * 2:vertex * 2 + 2] = [-.2, 1.2] if vertex == 6 else [0., 0.]
        mesh.write(list(range(mesh.vertices)), rows)
        mesh.set_settings(settings, restore=True)
        before[shape] = list(mesh.read(list(range(mesh.vertices))))
        expected[shape] = list(broken)
        meshes.append((transform, mesh, skin))
    cmds.select([item[0] for item in meshes], replace=True)
    with tempfile.TemporaryDirectory(prefix='qz-skin-') as directory:
        snapshot = action.skin_weights(op='snapshot', directory=directory)
        patches = []
        for entry in snapshot['meshes']:
            ids = expected[entry['mesh']]
            (Path(directory) / ('mesh-%d.issues' % entry['index'])).write_bytes(
                struct.pack('<%dI' % len(ids), *ids))
            patches.append(dict(index=entry['index'], invalid_count=len(ids)))
        issue = action.skin_weights(op='remember_issues', token=snapshot['token'], patches=patches)
        action.skin_weights(op='release', token=snapshot['token'])
    token = issue['issue_token']
    unrelated = cmds.createNode('transform', name='qzUnrelated')
    cmds.select(unrelated, replace=True)
    selected = action.skin_weights(op='select_issues', token=token)
    assert selected['count'] == 3 and not selected['errors'], selected
    selected_components = cmds.ls(selection=True, long=True, flatten=True)
    expected_components = {shape.rsplit('|', 1)[0] + '.vtx[%d]' % vertex
                           for shape, ids in expected.items() for vertex in ids}
    assert set(selected_components) == expected_components, (selected_components, expected_components)
    # 修复前改变选择，仍只处理检测模型；修复后恢复用户选择。
    cmds.select(unrelated, replace=True)
    cmds.flushUndo()
    repaired = action.skin_weights(op='hammer_issues', token=token)
    assert not repaired['errors'], repaired
    assert repaired['attempted'] == 3 and repaired['count'] == 3 and repaired['remaining'] == 0, repaired
    assert cmds.ls(selection=True, long=True) == ['|' + unrelated]
    for _, mesh, _ in meshes:
        rows = list(mesh.read(list(range(mesh.vertices))))
        assert not action._invalid_issue_ids(mesh, expected[mesh.shape])
        for vertex in range(mesh.vertices):
            if vertex not in expected[mesh.shape]:
                assert all(abs(a - b) < 1e-12 for a, b in zip(
                    rows[vertex * 2:vertex * 2 + 2], before[mesh.shape][vertex * 2:vertex * 2 + 2]))
    # 一次Undo撤销整次多模型锤式修复。
    cmds.undo()
    for _, mesh, _ in meshes:
        actual = list(mesh.read(list(range(mesh.vertices))))
        assert all(abs(a - b) < 1e-12 for a, b in zip(actual, before[mesh.shape])), mesh.shape
    # 非有限权重也走同一原生命令；只按实际读回的结果报告修复数。
    mesh = meshes[0][1]
    settings = mesh.settings()
    mesh.set_settings(settings)
    mesh.write([6, 8], [float('nan'), 1., float('inf'), 0.])
    mesh.set_settings(settings, restore=True)
    assert action._invalid_issue_ids(mesh, [6, 8]) == [6, 8]
    cmds.select([item[0] for item in meshes], replace=True)
    with tempfile.TemporaryDirectory(prefix='qz-skin-') as directory:
        snapshot = action.skin_weights(op='snapshot', directory=directory)
        patches = []
        for entry in snapshot['meshes']:
            ids = expected[entry['mesh']]
            (Path(directory) / ('mesh-%d.issues' % entry['index'])).write_bytes(struct.pack('<%dI' % len(ids), *ids))
            patches.append(dict(index=entry['index'], invalid_count=len(ids)))
        token = action.skin_weights(op='remember_issues', token=snapshot['token'], patches=patches)['issue_token']
        action.skin_weights(op='release', token=snapshot['token'])
    nonfinite = action.skin_weights(op='hammer_issues', token=token)
    assert not nonfinite['errors'], nonfinite
    actual_remaining = sum(len(action._invalid_issue_ids(mesh, expected[mesh.shape])) for _, mesh, _ in meshes)
    assert nonfinite['remaining'] == actual_remaining, nonfinite
    assert nonfinite['count'] + actual_remaining == 3, nonfinite
    negative = _negative_cases()
    return dict(ok=True, negative=negative, selected=selected, repaired=repaired, undo_restored=True,
                non_target_weights_unchanged=True, nonfinite=nonfinite)


def _negative_cases():
    import math
    from maya import cmds
    from maya_actions import skin_weights as action
    cmds.file(new=True, force=True)
    targets, before, originals, dq_before, settings_before = [], {}, {}, {}, {}
    expected = [[0., .949999, .050001, 0.], [0., 0., 1., 0.], [0., 1.91, .09, 0.]]
    for index in range(2):
        transform = cmds.polyPlane(name='qzNegative%d' % index, sx=4, sy=4)[0]
        joints = []
        for number in range(5):
            cmds.select(clear=True)
            joints.append(cmds.joint(name='qzNegJoint%d_%d' % (index, number), p=(number, 0, 0)))
        skin = cmds.skinCluster(joints, transform, tsb=True)[0]
        # 删除不参与权重的一个影响，验证 physical/logic 列不连续时按名字正确写入。
        cmds.skinPercent(skin, transform, tv=[(joints[0], 1.)], zeroRemainingInfluences=True)
        cmds.skinCluster(skin, edit=True, removeInfluence=joints[1])
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        mesh = action._Mesh(shape)
        assert mesh.width == 4
        cmds.setAttr(skin + '.normalizeWeights', 1)
        cmds.setAttr(skin + '.maintainMaxInfluences', True)
        cmds.setAttr(joints[0] + '.liw', True)
        cmds.setAttr(joints[0] + '.liw', lock=True)
        settings_before[shape] = mesh.settings()
        mesh.set_settings(settings_before[shape])
        rows = [.7, .2, .1, 0.] * mesh.vertices
        for vertex, values in ((6, [-.000002, .95, .050002, 0.]),
                               (7, [-.4, .1, 1.3, 0.]), (8, [-.02, 1.92, .1, 0.]),
                               (9, [0., 0., 0., 0.])):
            rows[vertex*4:vertex*4+4] = values
        mesh.write(list(range(mesh.vertices)), rows)
        mesh.fn.setBlendWeights(mesh.path, mesh.component(list(range(mesh.vertices))),
                               mesh.om.MDoubleArray([.25] * mesh.vertices))
        mesh.set_settings(settings_before[shape], restore=True)
        before[shape] = list(mesh.read(list(range(mesh.vertices))))
        dq_before[shape] = list(mesh.fn.getBlendWeights(mesh.path, mesh.component(list(range(mesh.vertices)))))
        originals[shape] = rows
        targets.append((transform, mesh))

    def remember(ids):
        cmds.select([t for t, _ in targets], replace=True)
        with tempfile.TemporaryDirectory(prefix='qz-skin-') as directory:
            snap = action.skin_weights(op='snapshot', directory=directory)
            patches = []
            for entry in snap['meshes']:
                (Path(directory) / ('mesh-%d.issues' % entry['index'])).write_bytes(struct.pack('<%dI' % len(ids), *ids))
                patches.append(dict(index=entry['index'], invalid_count=len(ids)))
            token = action.skin_weights(op='remember_issues', token=snap['token'], patches=patches)['issue_token']
            action.skin_weights(op='release', token=snap['token'])
        return token

    token = remember([6, 7, 8, 9])
    unrelated = cmds.createNode('transform', name='qzNegUnrelated')
    cmds.select(unrelated, replace=True)
    selection = cmds.ls(selection=True, long=True)
    cmds.flushUndo()
    result = action.skin_weights(op='fix_negative', token=token)
    assert (result['attempted'], result['count'], result['remaining']) == (6, 6, 2), result
    assert not result['errors'], result
    assert cmds.ls(selection=True, long=True) == selection
    after = {}
    for _, mesh in targets:
        rows = list(mesh.read(list(range(mesh.vertices))))
        after[mesh.shape] = rows
        for vertex in range(mesh.vertices):
            actual = rows[vertex*4:vertex*4+4]
            original = before[mesh.shape][vertex*4:vertex*4+4]
            if vertex in (6, 7, 8):
                assert all(abs(a-b) < 1e-14 for a,b in zip(actual, expected[vertex-6])), (vertex, actual)
                assert abs(math.fsum(actual)-math.fsum(original)) < 1e-14
                assert all(w >= 0 for w in actual) and actual[-1] == 0
            else:
                assert actual == original, (vertex, actual, original)
        assert mesh.settings() == settings_before[mesh.shape]
        assert list(mesh.fn.getBlendWeights(mesh.path, mesh.component(list(range(mesh.vertices))))) == dq_before[mesh.shape]
    cmds.undo()
    for _, mesh in targets:
        assert list(mesh.read(list(range(mesh.vertices)))) == before[mesh.shape], mesh.shape
        assert mesh.settings() == settings_before[mesh.shape]
    cmds.redo()
    for _, mesh in targets:
        assert list(mesh.read(list(range(mesh.vertices)))) == after[mesh.shape], mesh.shape
        assert mesh.settings() == settings_before[mesh.shape]
    # 非有限值、无法保持总和的负权重，数值修复按钮均原样保留。
    for _, mesh in targets:
        settings = mesh.settings()
        mesh.set_settings(settings)
        mesh.write([6, 7, 8], [float('nan'), 1., 0., 0., float('inf'), 0., 1., 0., -.5, .1, 0., 0.])
        mesh.set_settings(settings, restore=True)
    token = remember([6, 7, 8, 9])
    cmds.select(unrelated, replace=True)
    skipped = action.skin_weights(op='fix_negative', token=token)
    assert (skipped['attempted'], skipped['count'], skipped['remaining']) == (0, 0, 8), skipped
    assert not skipped['errors'], skipped
    for _, mesh in targets:
        values = list(mesh.read([6, 7, 8, 9]))
        assert math.isnan(values[0]) and math.isinf(values[4])
        assert values[8:12] == [-.5, .1, 0., 0.] and values[12:16] == [0.]*4
    assert cmds.ls(selection=True, long=True) == selection
    return dict(ok=True, fixed=result, skipped=skipped, undo_redo=True,
                sums_preserved=True, zero_influences_unchanged=True,
                locks_and_settings_restored=True, dq_unchanged=True, sparse_influence_indices=True)
