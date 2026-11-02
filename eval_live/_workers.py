"""Run opted-in collectors in separate processes; the parent owns persistence."""
from dataclasses import dataclass, field
import multiprocessing
from multiprocessing.connection import wait
import signal

from .benchmarks import ProcessError, RunContext
from .store import validate_tables


@dataclass
class Outcome:
    tables: dict = field(default_factory=dict)
    processes: list = field(default_factory=list)
    status: str = "success"
    error: str | None = None


def measure(function, arguments):
    context = RunContext(*arguments)
    outcome = Outcome(processes=context.processes)
    try:
        tables = function(context)
        validate_tables(tables, user=True)
        outcome.tables = tables
    except KeyboardInterrupt:
        outcome.status, outcome.error = "interrupted", "interrupted by user"
    except Exception as error:
        outcome.status = error.result.status if isinstance(error, ProcessError) else "failure"
        outcome.error = str(error)
    return outcome


def _interrupt(signum, frame):
    # RunContext.command catches this and terminates its subprocess group.
    raise KeyboardInterrupt


def _entry(connection, function, arguments):
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent coordinates cancellation
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        outcome = measure(function, arguments)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        connection.send(outcome)
    finally:
        connection.close()


class Worker:
    def __init__(self, function, arguments):
        context = multiprocessing.get_context("spawn")
        self.connection, sender = context.Pipe(duplex=False)
        self.process = context.Process(target=_entry, args=(sender, function, arguments))
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            raise
        finally:
            sender.close()

    def cancel(self):
        if self.process.is_alive():
            self.process.terminate()

    def result(self):
        try:
            return self.connection.recv()
        except EOFError:
            return Outcome(status="failure", error="collector process exited without returning a result")
        finally:
            self.connection.close()
            self.process.join()
            self.process.close()


def completed(workers):
    """Wait for any result, including a worker that exited unexpectedly."""
    ready = wait([worker.connection for worker in workers])
    return next(worker for worker in workers if worker.connection in ready)
