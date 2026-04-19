from threading import Thread

# Globaler Status für den Agenten
shutdown_requested = False
active_threads: list[Thread] = []
