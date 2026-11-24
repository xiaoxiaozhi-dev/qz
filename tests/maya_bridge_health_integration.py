"""Isolated GUI Maya only: real main-thread scheduling, never an existing scene."""
import threading
import time


def run():
    from maya import cmds, utils
    from maya_bridge import server
    from dcc_bridge import DccClient
    assert not cmds.about(batch=True), 'Use run_maya_native.py --gui'
    calls = []

    def request(client, action):
        result = {}
        def send():
            try:
                result['response'] = client.call(action, timeout=8)
            except BaseException as exc:
                result['error'] = repr(exc)
        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            utils.processIdleEvents()
            time.sleep(.005)
        thread.join(.1)
        assert not thread.is_alive() and 'error' not in result, result
        return result['response']

    server.run(port=0)
    client = DccClient(port=server._server_sock.getsockname()[1])
    try:
        ready = request(client, 'ping')
        assert ready['ok'] and ready['data']['main_thread_ready'], ready
        assert ready['data']['maya_version'] == cmds.about(version=True), ready
        assert request(client, 'bridge_connect')['ok']
        waiting, probe_ids, errors = [], [], []
        def observe():
            try:
                time.sleep(.3)
                for _ in range(8):
                    waiting.append(client.ping())
                    probe_ids.append(id(server._heartbeat_probe))
                    time.sleep(.75)
            except BaseException as exc:
                errors.append(repr(exc))
        collector = threading.Thread(target=observe, daemon=True)
        collector.start()
        # A genuine 12-second main-thread stall outside any bridge action.
        time.sleep(12)
        collector.join(1)
        assert not collector.is_alive() and not errors, errors
        assert len(waiting) == 8 and len(set(probe_ids)) == 1
        assert all(r['ok'] and not r['data']['main_thread_ready'] and not r['data']['busy'] for r in waiting), waiting
        recovered = request(client, 'ping')
        assert recovered['data']['main_thread_ready'], recovered

        entered, stop = threading.Event(), threading.Event()
        busy = []
        def slow_action():
            calls.append('executed')
            entered.set()
            time.sleep(4.5)
            return {'finished': True}
        server.ACTIONS['qz_health_slow_test'] = slow_action
        def observe_busy():
            entered.wait(5)
            while not stop.is_set():
                busy.append(client.ping())
                stop.wait(.3)
        watcher = threading.Thread(target=observe_busy, daemon=True)
        watcher.start()
        try:
            result = request(client, 'qz_health_slow_test')
            assert result['ok'] and result['data']['finished'], result
        finally:
            stop.set()
            # Drain the one pending read-only probe before joining.
            utils.processIdleEvents()
            watcher.join(1)
        assert calls == ['executed']
        assert sum(bool(r['data'].get('busy')) for r in busy) >= 2, busy
        detached = request(client, 'bridge_disconnect')
        assert detached['ok'] and detached['data']['stopped'], detached
        assert server._server_sock is None
        return dict(ok=True, real_main_thread_dispatch=True, main_block_seconds=12,
                    waiting_pings=len(waiting), pending_probes=len(set(probe_ids)),
                    busy_pings=len(busy), business_executions=len(calls),
                    recovered=recovered, detached=detached)
    finally:
        server.ACTIONS.pop('qz_health_slow_test', None)
        server.stop()
