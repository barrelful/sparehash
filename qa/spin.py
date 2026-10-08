#!/usr/bin/env python3
"""CPU spin fallback for contention testing when stress-ng is unavailable."""
import multiprocessing
import sys
import time


def spin(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _ = sum(range(10000))


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    workers = multiprocessing.cpu_count()
    processes = []
    for _ in range(workers):
        proc = multiprocessing.Process(target=spin, args=(seconds,))
        proc.start()
        processes.append(proc)
    for proc in processes:
        proc.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
