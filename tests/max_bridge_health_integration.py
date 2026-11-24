# -*- coding: utf-8 -*-
"""Separate Max2020 batch only; outer MAXScript pumps real worker timers."""
from __future__ import unicode_literals
import io
import json
import os
import socket
import sys
import threading
import time
import traceback

OUTPUT = os.environ['QZ_HEALTH_OUTPUT']
sys.path.insert(0, os.environ['QZ_HEALTH_PROJECT'])
released = threading.Event()


def write(name, value):
    with io.open(os.path.join(OUTPUT, name), 'w', encoding='utf-8') as file:
        file.write(type(u'')(json.dumps(value, ensure_ascii=True, indent=2)))


def begin():
    from pymxs import runtime as rt
    assert rt.maxOps.isInNonInteractiveMode(), 'Separate batch process only'
    assert sys.version_info[:2] == (2, 7), sys.version
    from max_bridge import server, start_max
    server.WORKER_FAILURE_PATH = os.path.join(OUTPUT, 'worker.error')
    assert start_max.start(port=0)
    port = server._server_sock.getsockname()[1]
    executions, entered = [], threading.Event()
    def slow():
        executions.append('executed')
        entered.set()
        time.sleep(4.5)
        return {'finished': True}
    server.ACTIONS['qz_health_slow_test'] = slow

    def request(action, **params):
        conn = socket.socket()
        conn.settimeout(8)
        try:
            conn.connect(('127.0.0.1', port))
            conn.sendall((json.dumps({'action': action, 'params': params}) + '\n').encode('utf-8'))
            data = b''
            while b'\n' not in data:
                chunk = conn.recv(4096)
                assert chunk
                data += chunk
            return json.loads(data.decode('utf-8'))
        finally:
            conn.close()

    def observe():
        try:
            first = request('ping')
            assert first['ok'] and first['data']['main_thread_ready'], first
            assert first['data']['is_main'] and first['data']['max_version'] == '2020', first
            assert request('bridge_connect', client_id='isolated-health-test')['ok']
            write('ready.json', {'ok': True})
            time.sleep(.3)
            waiting, ids = [], []
            for _ in range(8):
                waiting.append(request('ping'))
                ids.append(id(server._heartbeat_task))
                time.sleep(.75)
            assert len(set(ids)) == 1 and server.pending_count() == 1, ids
            assert all(r['ok'] and not r['data']['main_thread_ready'] and not r['data']['busy'] for r in waiting), waiting
            assert released.wait(20)
            recovered = request('ping')
            assert recovered['data']['main_thread_ready'], recovered
            business = []
            worker = threading.Thread(target=lambda: business.append(request('qz_health_slow_test')))
            worker.daemon = True
            worker.start()
            assert entered.wait(5)
            busy = [request('ping') for _ in range(4)]
            assert all(row['data']['busy'] for row in busy), busy
            worker.join(7)
            assert not worker.is_alive() and business[0]['ok'] and executions == ['executed'], business
            write('worker.error', {'test': 'worker stopped'})
            failure = request('ping')
            assert not failure['ok'] and failure['data']['worker_failed'], failure
            os.remove(server.WORKER_FAILURE_PATH)
            detached = request('bridge_disconnect')
            assert detached['ok'] and detached['data']['stopped'], detached
            assert server._server_sock is None
            result = dict(ok=True, python=sys.version, waiting_pings=len(waiting), pending_probes=len(set(ids)),
                          main_block_seconds=12, busy_pings=len(busy), business_executions=len(executions),
                          worker_failure_detected=True, recovered=recovered, detached=detached)
        except BaseException:
            result = dict(ok=False, error=traceback.format_exc())
            write('ready.json', result)
        write('result.json', result)
    thread = threading.Thread(target=observe)
    thread.daemon = True
    thread.start()
