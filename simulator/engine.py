"""
1. MINIMAL DISCRETE-EVENT ENGINE  (replaces the simpy dependency)
======================================================================
Three primitives only: Environment.timeout(), Lock (a FIFO mutex with
optional acquire-timeout), and Process.raise_interrupt() (an OS-style
preemption signal). This is intentionally small enough to read in five
minutes — the entire "kernel model" you're testing against is right here.
"""

from __future__ import annotations
import heapq
import itertools
from collections import deque
from typing import Optional


class Preempted(Exception):
    """Raised inside a process's generator when the OS preempts it while
    it is mid-flight on a plain timeout (i.e. actually holding the CPU)."""
    def __init__(self, cause=None):
        super().__init__(cause)
        self.cause = cause


class Environment:
    def __init__(self):
        self.now = 0.0
        self._heap = []
        self._seq = itertools.count()
        self._immediate = deque()
        self.active_process = None

    def _schedule(self, delay, callback):
        timer = [self.now + delay, next(self._seq), callback, False]
        heapq.heappush(self._heap, timer)
        return timer

    def _cancel(self, timer):
        timer[3] = True

    def _schedule_now(self, callback):
        self._immediate.append(callback)

    def timeout(self, delay):
        return ("timeout", max(delay, 0.0))

    def process(self, gen):
        return Process(self, gen)

    def run(self, until):
        while True:
            while self._immediate:
                self._immediate.popleft()()
            if not self._heap or self._heap[0][0] > until:
                break
            timer = heapq.heappop(self._heap)
            if timer[3]:
                continue
            self.now = timer[0]
            timer[2]()
        self.now = until


class Process:
    """Drives a transaction/scheduler generator. Yield ('timeout', ms) to
    wait, or a Lock.request(...) instruction to acquire a mutex."""

    def __init__(self, env: Environment, gen):
        self.env = env
        self.gen = gen
        self.timer = None
        self.alive = True
        env._schedule_now(self._start)

    def _start(self):
        self._step(self.gen.send, None)

    def _step(self, driver, arg):
        prev = self.env.active_process
        self.env.active_process = self
        try:
            instr = driver(arg)
        except StopIteration:
            self.alive = False
            self.env.active_process = prev
            return
        finally:
            self.env.active_process = prev
        self._handle_instr(instr)

    def _handle_instr(self, instr):
        kind = instr[0]
        if kind == "timeout":
            self.timer = self.env._schedule(instr[1], self._on_timer)
        elif kind == "lock":
            _, lock, timeout = instr
            lock._acquire(self, timeout)
        else:
            raise RuntimeError(f"unknown yield instruction: {instr!r}")

    def _on_timer(self):
        self.timer = None
        self._step(self.gen.send, None)

    def resume_value(self, value):
        self._step(self.gen.send, value)

    def raise_interrupt(self, cause=None):
        """OS-style preemption. Only has an effect if the process is
        currently holding the CPU on a plain timeout (i.e. inside
        run_operation) — a process merely queued for a lock is not
        'running' and so cannot be preempted off a core it doesn't hold."""
        if self.timer is not None:
            self.env._cancel(self.timer)
            self.timer = None
            self._step(self.gen.throw, Preempted(cause))
            return True
        return False


class Lock:
    """A FIFO mutex used both for per-record locks (2PL) and for the core
    itself (only one transaction runs on a core at a time)."""

    def __init__(self, env: Environment):
        self.env = env
        self.owner = None
        self._waiters = []

    def request(self, timeout: Optional[float] = None):
        return ("lock", self, timeout)

    def _acquire(self, proc: Process, timeout):
        if self.owner is None:
            self.owner = proc
            self.env._schedule_now(lambda: proc.resume_value(True))
            return
        entry = {"proc": proc, "timer": None, "done": False}
        if timeout is not None:
            def on_timeout():
                if entry["done"]:
                    return
                entry["done"] = True
                if entry in self._waiters:
                    self._waiters.remove(entry)
                proc.resume_value(False)
            entry["timer"] = self.env._schedule(timeout, on_timeout)
        self._waiters.append(entry)

    def release(self):
        self.owner = None
        while self._waiters:
            entry = self._waiters.pop(0)
            if entry["done"]:
                continue
            entry["done"] = True
            if entry["timer"] is not None:
                self.env._cancel(entry["timer"])
            self.owner = entry["proc"]
            self.env._schedule_now(lambda p=entry["proc"]: p.resume_value(True))
            return
