"""Portable baseline CPU load. This is not a calibrated hardware benchmark."""
import argparse
import hashlib
import multiprocessing
import os
import signal
import time


def worker(stop):
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    data = b"OCRUN baseline CPU workload" * 256
    while not stop.is_set():
        for _ in range(1000):
            data = hashlib.sha256(data).digest() * 128


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1):
        parser.error("workers must not exceed the logical CPU count")
    stop = multiprocessing.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    processes = []
    failed = False
    try:
        for _ in range(args.workers):
            process = multiprocessing.Process(target=worker, args=(stop,))
            process.start()
            processes.append(process)
        print("OCRUN baseline CPU workload, workers={}".format(args.workers), flush=True)
        while not stop.wait(0.5):
            if any(not process.is_alive() for process in processes):
                failed = True
                break
    finally:
        stop.set()
        for process in processes:
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
