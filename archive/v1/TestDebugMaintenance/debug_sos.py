# debug_sos.py
import asyncio, faulthandler, signal, sys, threading, traceback, os
import time
import logging

_logf = open("/tmp/py-stacks.log", "a", buffering=1)  # line-buffered append
faulthandler.enable(all_threads=True, file=_logf)

def dump_all_stacks(label="SOS"):
    """free and safe to keep on in production"""

    out = _logf
    print(f"\n======== {label} (pid={os.getpid()}) ========", file=out)
    faulthandler.dump_traceback(file=out, all_threads=True)
    frames = sys._current_frames()
    for th in threading.enumerate():
        fr = frames.get(getattr(th, "ident", None))
        print(f"\n-- [Thread] {th.name}", file=out)
        traceback.print_stack(fr, file=out) if fr else print("(no frame)", file=out)
    try:
        loop = asyncio.get_running_loop()
        tasks = list(asyncio.all_tasks(loop))
        print(f"\n-- asyncio tasks: {len(tasks)}", file=out)
        for t in tasks:
            print(f"\nTask: {t!r} canc={t.cancelled()} done={t.done()}", file=out)
            for fr in t.get_stack() or []:
                traceback.print_stack(fr, file=out)
    except RuntimeError:
        print("\n(no running asyncio loop)", file=out)
    print("======== END ========\n", file=out)

def install_signal_handlers():
    """debugging - turn off when the program is stable"""
    # DO NOT register USR1 (it can kill the proc if handler isn’t ready)
    faulthandler.dump_traceback_later(60, repeat=True, file=_logf)  # every 60s
    def _handler(signum, frame): dump_all_stacks(label=f"SIGNAL {signum}")
    signal.signal(signal.SIGUSR2, _handler)
    print(f"[sos] handlers installed for SIGUSR2; PID={os.getpid()}", file=_logf)

async def task_census(interval=120, include_stacks=False):
    log = logging.getLogger("tasks")
    while True:
        await asyncio.sleep(interval)
        tasks = [t for t in asyncio.all_tasks() if not t.done()]
        log.info("📊 %d running tasks:", len(tasks))
        if include_stacks:
            for t in tasks[:60]:
                stack = t.get_stack()
                pretty = "".join(asyncio.format_stack(stack)) if stack else "(no stack)"
                log.info("• %r\n%s", t, pretty)
        else:
            for t in tasks[:60]:
                log.info("• %r (done=%s, cancelled=%s)", t, t.done(), t.cancelled())

async def loop_watchdog(threshold_ms=300, interval=1.0, dump_on_stall=True):
    log = logging.getLogger("watchdog")
    loop = asyncio.get_running_loop()
    last = loop.time()
    while True:
        await asyncio.sleep(interval)
        now = loop.time()
        drift_ms = (now - last - interval) * 1000
        if drift_ms > threshold_ms:
            log.warning("⏳ Event loop stall: drift=%.0f ms (> %d ms)", drift_ms, threshold_ms)
            if dump_on_stall:
                os.kill(os.getpid(), signal.SIGUSR2)  # write stack dump
        last = now