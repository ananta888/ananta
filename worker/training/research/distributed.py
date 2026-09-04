"""Bounded torchrun process-group lifecycle for one delegated stage."""

from __future__ import annotations

import os
from dataclasses import dataclass

from worker.training.research.modeling import require_torch


@dataclass(frozen=True, slots=True)
class DistributedRuntime:
    world_size: int
    rank: int
    initialized_here: bool

    @classmethod
    def ensure(cls, expected_world_size: int) -> DistributedRuntime:
        torch = require_torch()
        if not 1 <= expected_world_size <= 1024:
            raise ValueError("research_distributed_world_size_invalid")
        if expected_world_size == 1:
            if torch.distributed.is_available() and torch.distributed.is_initialized():
                actual = torch.distributed.get_world_size()
                if actual != 1:
                    raise ValueError("research_distributed_world_size_mismatch")
            return cls(1, 0, False)
        if not torch.distributed.is_available():
            raise RuntimeError("research_distributed_runtime_unavailable")
        initialized_here = False
        if not torch.distributed.is_initialized():
            actual = int(os.environ.get("WORLD_SIZE", "0"))
            if actual != expected_world_size:
                raise ValueError("research_distributed_world_size_mismatch")
            cuda_available = torch.cuda.is_available()
            if cuda_available:
                local_rank = int(os.environ.get("LOCAL_RANK", "-1"))
                if not 0 <= local_rank < torch.cuda.device_count():
                    raise ValueError("research_distributed_local_rank_invalid")
                torch.cuda.set_device(local_rank)
            backend = "nccl" if cuda_available else "gloo"
            torch.distributed.init_process_group(backend=backend, init_method="env://")
            initialized_here = True
        actual = torch.distributed.get_world_size()
        if actual != expected_world_size:
            raise ValueError("research_distributed_world_size_mismatch")
        return cls(actual, torch.distributed.get_rank(), initialized_here)

    @property
    def primary(self) -> bool:
        return self.rank == 0

    def close(self) -> None:
        torch = require_torch()
        if self.world_size > 1 and torch.distributed.is_initialized():
            torch.distributed.barrier()
            if self.initialized_here:
                torch.distributed.destroy_process_group()


__all__ = ["DistributedRuntime"]
