#!/usr/bin/env python3
"""Sample physical footprint and wakeups for an app AND its disposable helpers.

Usage: python scripts/profile_native_macos.py PID --seconds 30
Interact with the app during the interval to measure collection/sync peaks.
No usage contents, command arguments, or account identifiers are collected.
Physical footprint comes from libproc, using Apple's rusage_info_v4 layout.
"""
import argparse
import ctypes
import json
import statistics
import sys
import time

import psutil


class Usage(ctypes.Structure):
    _fields_ = [('uuid', ctypes.c_uint8 * 16), ('values', ctypes.c_uint64 * 36)]


class Timebase(ctypes.Structure):
    _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    if sys.platform != 'darwin' or not 0 < args.seconds <= 600:
        parser.error('Requires macOS and a duration from 0 to 600 seconds')
    library = ctypes.CDLL('/usr/lib/libproc.dylib')
    library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    library.proc_pid_rusage.restype = ctypes.c_int
    timebase = Timebase()
    ctypes.CDLL('/usr/lib/libSystem.B.dylib').mach_timebase_info(ctypes.byref(timebase))
    seconds_per_tick = timebase.numer / timebase.denom / 1e9
    root = psutil.Process(args.pid)
    samples, seen, initial, last = [], set(), {}, {}
    started = time.monotonic()
    while time.monotonic() - started < args.seconds:
        processes = [root]
        try:
            processes += root.children(recursive=True)
        except psutil.NoSuchProcess:
            break
        total, app, helpers = 0, 0, 0
        for process in processes:
            value = Usage()
            if library.proc_pid_rusage(process.pid, 4, ctypes.byref(value)):
                continue
            memory = value.values[7]
            total += memory
            if process.pid == args.pid:
                app = memory
            else:
                helpers += 1
                seen.add(process.name())
            # Monotonic per-process counters; report sampled CPU, including exited helpers.
            counters = (value.values[0] + value.values[1], value.values[2], value.values[3])
            if process.pid not in initial:
                initial[process.pid] = counters if not samples else (0, 0, 0)
            last[process.pid] = counters
        samples.append((app, total, helpers))
        time.sleep(.1)
    elapsed = time.monotonic() - started
    deltas = [sum(max(0, last[pid][i] - initial[pid][i]) for pid in last) for i in range(3)]
    print(json.dumps({'seconds': round(elapsed, 2),
                      'app_median_mib': round(statistics.median(row[0] for row in samples) / 2**20, 2),
                      'app_final_mib': round(samples[-1][0] / 2**20, 2),
                      'tree_peak_mib': round(max(row[1] for row in samples) / 2**20, 2),
                      'sampled_cpu_percent': round(deltas[0] * seconds_per_tick / elapsed * 100, 3),
                      'package_idle_wakeups': deltas[1], 'interrupt_wakeups': deltas[2],
                      'max_helpers': max(row[2] for row in samples), 'helper_names': sorted(seen)}, indent=2))


if __name__ == '__main__':
    main()
