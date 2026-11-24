/*
    max_bridge/bootstrap.ms
    Create an invisible rollout whose timer drives worker.py on the MAIN thread.

    WHY THIS TIMER IS MANDATORY
    pymxs can only be called from the main thread, but the socket server needs
    a thread. The MAXScript rollout timer is driven by Max's own message loop,
    so the callback is always on the main thread -- it is the only channel that
    can hand queued tasks back to the main thread.

    Measured (3ds Max 2027 build 3590):
        interval=100ms -> 9.1 ticks/sec, callback thread always MainThread

    THIS FILE IS INTENTIONALLY DULL
    Every construct below is one that has actually been executed by Max without
    error. Do not get clever here -- MAXScript has several traps that fail at
    runtime with no useful context. All of them are listed below.

    KEEP THIS FILE PURE ASCII
    MAXScript / the Listener do not reliably round-trip UTF-8 source text.
    Non-ASCII characters come back as mojibake in error messages.

    PITFALLS -- ALL HIT DURING DEVELOPMENT, DO NOT REVERT

    1. The timer control REQUIRES a name argument:
           timer tmrTick "qzClock" interval:100
       Omitting the name is a syntax error and the timer is never created.

    2. The tick handler must be a standalone block introduced by
       "on tmrTick tick do". An inline handler is not accepted.

    3. The dialog must be invisible. There is NO "#style_hidden" in MAXScript,
       so #style_no_caption + #style_toolwindow alone still leaves a small
       1x1 bordered box sitting in the viewport (confirmed on 2027).
       The fix that actually works: create it, then push it far off-screen
       via the rollout's own .pos property. Size and pos are writable on a
       rollout handle once CreateDialog has run.

    4. pymxs has NO python.UpdateSysPath. It was invented here and Max rejected
       it at runtime:
           undefined: "UpdateSysPath" resides in <Interface:python>
       There is also no need -- start_max.py puts the project root on sys.path
       before executing this file, so the worker's "import max_bridge.server"
       already resolves.

    5. MAXScript has NO implicit string continuation, so a call cannot be split
       across source lines with a leading plus sign. That is a syntax error
       reported as: "syntax error: at +, expected <factor>".
       Never build multi-line strings here. Keep every call on one line.

    6. try and catch are two separate BLOCKS. Writing them as a single
       "try(...) catch()" expression is not valid -- do not "tidy it up".

    7. Do not declare a rollout inside a conditional or nested block.
       Keep the rollout at the top level of the script.

    8. Do not embed Python source inside MAXScript string literals.
       All Python-side work (sys.path, socket server, action loading) belongs
       in start_max.py, which runs before this file.

    Usage: executed by start_max.py via pymxs.runtime.execute().
*/

-- Dispose the OLD rollout before redefining its global value.
global qzBridgeRollout
if qzBridgeRollout != undefined do
(
    try
    (
        destroyDialog qzBridgeRollout
    )
    catch
    (
        format "[qz-bridge] destroyDialog failed, continuing\n"
    )
)
global qzBridgeEnabled = true
rollout qzBridgeRollout "Qz Bridge" width:1 height:1
(
    local workerBusy = false
    local workerFailed = false
    local lastWorkerError = ""
    timer tmrTick "qzClock" interval:100

    on tmrTick tick do
    (
        -- UI operations may process messages while Python is still running.
        -- Pause ticks until the call returns; fail once instead of looping.
        if not workerBusy and not workerFailed do
        (
            workerBusy = true
            tmrTick.active = false
            try
            (
                local result = python.ExecuteFile "@WORKER@"
                if result != #success do
                (
                    workerFailed = true
                    lastWorkerError = "worker returned " + (result as string)
                )
            )
            catch
            (
                workerFailed = true
                lastWorkerError = getCurrentException()
            )
            workerBusy = false
            if workerFailed then
            (
                -- Wake socket waiters without re-entering the failed Python VM.
                local marker = undefined
                try
                (
                    marker = createFile "@FAILURE@"
                    if marker != undefined do
                    (
                        format "worker-failed\n" to:marker
                        close marker
                        marker = undefined
                    )
                )
                catch
                (
                    if marker != undefined do close marker
                    format "[qz-bridge] could not write worker failure marker\n"
                )
                format "[qz-bridge] worker stopped after error: %\n" lastWorkerError
            )
            else
            (
                tmrTick.active = qzBridgeEnabled
            )
        )
    )
)

CreateDialog qzBridgeRollout style:#(#style_no_caption, #style_toolwindow)
qzBridgeRollout.tmrTick.active = true

-- Do NOT touch the rollout's size or position here.
--   A Rollout has no size property (2027 throws 'unknown property'),
--   and the thrown error aborts every following statement.
--   Hiding is done from start_max.py via Win32 ShowWindow(SW_HIDE).

format "[qz-bridge] rollout timer started, interval=100ms\n"
