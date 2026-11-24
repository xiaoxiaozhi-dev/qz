"""Use run_maya_native.py: real Maya commands and TCP with a batch main-thread queue."""
import queue
import socket
import threading
import time


def run():
    from maya import cmds
    from maya_bridge import server
    from dcc_bridge import DccClient
    assert cmds.about(batch=True)
    jobs = queue.Queue()
    original = server._run_in_main

    def in_main(fn):
        done = threading.Event()
        holder = {}
        jobs.put((fn, done, holder))
        assert done.wait(5), 'Batch main-thread queue did not execute'
        if 'error' in holder:
            raise holder['error']
        return holder['result']

    def request(client, action, **kwargs):
        holder = {}
        def send():
            try:
                holder['result'] = client.call(action, **kwargs)
            except BaseException as exc:
                holder['error'] = exc
        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while worker.is_alive() and time.monotonic() < deadline:
            try:
                fn, done, result = jobs.get(timeout=.01)
            except queue.Empty:
                continue
            try:
                result['result'] = fn()
            except BaseException as exc:
                result['error'] = exc
            done.set()
        worker.join(.1)
        assert not worker.is_alive()
        if 'error' in holder:
            raise holder['error']
        return holder['result']

    # Maya's production executeInMainThreadWithResult is unavailable in batch.
    # Only this disposable test replaces its scheduling, not actions or socket IO.
    server._run_in_main = in_main
    reports = []
    try:
        port = 0
        for cycle in range(2):
            server.run(port=port)
            port = server._server_sock.getsockname()[1]
            client = DccClient(port=port)
            ping = request(client, 'ping')
            assert ping['ok'] and ping['data']['maya_version'] == cmds.about(version=True)
            connected = request(client, 'bridge_connect', client_id='native-test')
            assert connected['ok'] and connected['data']['connected']
            disconnected = request(client, 'bridge_disconnect')
            assert disconnected['ok'] and disconnected['data']['stopped']
            assert server._server_sock is None
            with socket.socket() as probe:
                assert probe.connect_ex(('127.0.0.1', port)) != 0
            reports.append(dict(cycle=cycle, port=port, ping=ping, disconnected=disconnected))
            time.sleep(.03)
    finally:
        server.stop()
        server._run_in_main = original
    return dict(ok=True, reports=reports, batch_dispatch='test main-thread queue')
