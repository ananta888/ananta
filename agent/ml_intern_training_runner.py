"""Deprecated import shim for the isolated LoRA worker child entrypoint.

The Hub must never execute model training.  Existing process supervisors that
still reference ``agent.ml_intern_training_runner`` are forwarded to the
worker-owned implementation and therefore use its strict ``--context`` /
``--result`` contract.  The former path-based ``--spec`` contract is retired.
"""

from worker.training.job_process import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
