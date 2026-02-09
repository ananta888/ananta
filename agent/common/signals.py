import os
import signal
import logging
import threading
import agent.common.context

def setup_signal_handlers():
    """Registriert Signal-Handler für SIGTERM und SIGINT."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    logging.info("Signal-Handler für SIGTERM und SIGINT registriert.")

def _handle_shutdown(signum, frame):
    if agent.common.context.shutdown_requested:
        return
    
    pid = os.getpid()
    logging.info(f"Shutdown Signal {signum} empfangen (PID: {pid})...")
    
    agent.common.context.shutdown_requested = True
    
    # Ressourcen-Bereinigung in separatem Thread ausführen, 
    # um den Signal-Handler nicht zu blockieren und Reentrancy-Probleme zu vermeiden.
    cleanup_thread = threading.Thread(target=_perform_cleanup, name="ShutdownCleanup")
    cleanup_thread.start()

def _perform_cleanup():
    """Führt die tatsächliche Bereinigung der Ressourcen durch."""
    logging.info("Beginne Ressourcen-Bereinigung...")
    
    # Shell schließen
    try:
        from agent.shell import get_shell
        get_shell().close()
    except Exception as e:
        logging.debug(f"Fehler beim Schließen der Shell (evtl. noch nicht initialisiert): {e}")
    
    # Scheduler stoppen
    try:
        from agent.scheduler import get_scheduler
        get_scheduler().stop()
    except Exception as e:
        logging.debug(f"Fehler beim Stoppen des Schedulers: {e}")

    # Shell-Pool schließen
    try:
        from agent.shell import get_shell_pool
        get_shell_pool().close_all()
    except Exception as e:
        logging.debug(f"Fehler beim Schließen des Shell-Pools: {e}")

    # Hintergrund-Threads sauber beenden
    threads_to_join = list(agent.common.context.active_threads)
    if threads_to_join:
        logging.info(f"Beende {len(threads_to_join)} Hintergrund-Threads...")
        for t in threads_to_join:
            if t.is_alive() and t != threading.current_thread():
                try:
                    t.join(timeout=1)
                except Exception:
                    pass
        logging.info("Hintergrund-Threads wurden (soweit möglich) beendet.")
    
    logging.info("Shutdown-Vorgang abgeschlossen.")
