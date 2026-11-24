"""Execute twice from an isolated Max2020 batch MAXScript pumping Windows messages."""
from __future__ import print_function, unicode_literals
import io
import json
import os
import socket
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUTPUT = os.environ['QZ_BRIDGE_NATIVE_DIR']


def write(name, data):
    text = json.dumps(data, ensure_ascii=True, indent=2)
    if not isinstance(text, type(u'')):
        text = text.decode('ascii')
    with io.open(os.path.join(OUTPUT, name), 'w', encoding='utf-8') as stream:
        stream.write(text)


def run_round():
    from pymxs import runtime as rt
    assert rt.maxOps.isInNonInteractiveMode(), 'Only a separate batch process is allowed'
    assert sys.version_info[0] == 2, sys.version
    from max_bridge import server, start_max
    state = getattr(sys, '_qz_connection_native', None)
    if state is None:
        state = {'port': 0, 'reports': [], 'round': 0}
        sys._qz_connection_native = state
    state['round'] += 1
    number = state['round']
    assert start_max.start(port=state['port'])
    state['port'] = server._server_sock.getsockname()[1]

    def request(action, **params):
        conn = socket.socket()
        conn.settimeout(5)
        try:
            conn.connect(('127.0.0.1', state['port']))
            conn.sendall((json.dumps({'action': action, 'params': params}) + '\n').encode('utf-8'))
            data = b''
            while b'\n' not in data:
                chunk = conn.recv(4096)
                assert chunk
                data += chunk
            return json.loads(data.decode('utf-8'))
        finally:
            conn.close()

    def check():
        try:
            ping = request('ping')
            assert ping['ok'] and ping['data']['max_version'] == '2020', ping
            assert ping['data']['is_main'], ping
            attached = request('bridge_connect', client_id='native-test')
            assert attached['ok'], attached
            server._business_requests += 1
            try:
                busy = request('bridge_disconnect')
                assert not busy['ok'] and busy.get('error'), busy
                assert server._server_sock is not None
            finally:
                server._business_requests -= 1
            detached = request('bridge_disconnect')
            assert detached['ok'] and detached['data']['stopped'], detached
            assert server._server_sock is None
            probe = socket.socket()
            try:
                assert probe.connect_ex(('127.0.0.1', state['port'])) != 0
            finally:
                probe.close()
            time.sleep(.15)
            ticks = sys._qz_worker_state['ticks']
            time.sleep(.3)
            assert sys._qz_worker_state['ticks'] == ticks, 'Timer resumed after disconnect'
            report = dict(ok=True, round=number, port=state['port'], ping=ping, ticks_stopped=ticks,
                          python=sys.version, busy_rejected=busy)
        except BaseException:
            report = dict(ok=False, error=traceback.format_exc(), round=number, port=state['port'],
                          worker=dict(getattr(sys, '_qz_worker_state', {})), pending=server.pending_count())
        state['reports'].append(report)
        if number == 2:
            write('result.json', dict(ok=all(row['ok'] for row in state['reports']), reports=state['reports']))
        write('round-%d.json' % number, report)
    thread = threading.Thread(target=check)
    thread.daemon = True
    thread.start()


try:
    run_round()
except BaseException:
    result = dict(ok=False, error=traceback.format_exc())
    write('result.json', result)
    write('round-1.json', result)
    write('round-2.json', result)
