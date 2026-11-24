"""
max_bridge/worker.py
Main-thread worker repeatedly executed by the MAXScript rollout timer.

Keep this file ASCII with NO encoding cookie. Max 2020 ExecuteFile passes
Unicode source to Python 2.7, which rejects an encoding declaration there.
Keep module execution light and never block or sleep on the Max UI thread.
Store persistent state on sys because ExecuteFile reruns the module body.
Socket threads enqueue requests; this worker only drains the queue.
"""

import sys

# Persist across ExecuteFile calls; module globals are not persistent.
_STATE_ATTR = "_qz_worker_state"

if not hasattr(sys, _STATE_ATTR):
    sys._qz_worker_state = {
        "ticks": 0,
        "errors": 0,
        "handled": 0,
        "last_error": "",
    }


def _state():
    return getattr(sys, _STATE_ATTR)


def pump(max_items=64):
    """Drain queued actions on one timer tick; return the processed count."""
    st = _state()
    st["ticks"] += 1
    if st.get("busy", False):
        st["reentrant_skips"] = st.get("reentrant_skips", 0) + 1
        return 0
    st["busy"] = True
    try:
        import max_bridge.server as srv
        n = srv.drain(max_items=max_items)
        st["handled"] += n
        return n
    except Exception:
        import traceback
        st["errors"] += 1
        st["last_error"] = traceback.format_exc()
        return 0
    finally:
        st["busy"] = False


def status():
    """Return a diagnostic snapshot from the main thread."""
    st = _state()
    info = {
        "ticks": st["ticks"],
        "handled": st["handled"],
        "errors": st["errors"],
        "last_error": st["last_error"],
        "busy": st.get("busy", False),
        "reentrant_skips": st.get("reentrant_skips", 0),
    }
    try:
        import max_bridge.server as srv
        info["pending"] = srv.pending_count()
        info["actions"] = sorted(srv.ACTIONS.keys())
    except Exception as e:
        info["server_error"] = repr(e)
    return info


# One lightweight call per ExecuteFile invocation.
pump()
