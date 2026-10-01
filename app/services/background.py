"""Threads that must outlive a request (a streamed chat turn whose client went away).

They are tracked on the app so shutdown, and the tests, can wait for them to finish.
"""

import threading
from collections.abc import Callable


def start(app, target: Callable[[], None], name: str) -> threading.Thread:
    if not hasattr(app.state, "background_threads"):
        app.state.background_threads = set()
    threads: set[threading.Thread] = app.state.background_threads

    def run() -> None:
        try:
            target()
        finally:
            threads.discard(threading.current_thread())

    thread = threading.Thread(target=run, name=name, daemon=True)
    threads.add(thread)
    thread.start()
    return thread


def wait_for_all(app, timeout: float = 60.0) -> None:
    for thread in list(getattr(app.state, "background_threads", ())):
        thread.join(timeout)
