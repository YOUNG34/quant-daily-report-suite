from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from quanttrade.strategy.base import Strategy, StrategyMetadata

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StrategyVersion:
    name: str
    version: str
    factory: Callable[[], Strategy]
    meta: StrategyMetadata
    active: bool = True


class StrategyRegistry:
    """Simple in-memory strategy registry for CI and rollout."""

    def __init__(self) -> None:
        self._strategies: Dict[tuple[str, str], StrategyVersion] = {}

    def register(self, name: str, version: str, factory: Callable[[], Strategy], meta: StrategyMetadata) -> None:
        key = (name, version)
        if key in self._strategies:
            logger.warning("Strategy %s@%s already registered; replacing", name, version)
        self._strategies[key] = StrategyVersion(name=name, version=version, factory=factory, meta=meta)
        logger.info("Registered strategy %s@%s", name, version)

    def get(self, name: str, version: Optional[str] = None) -> Strategy:
        if version:
            key = (name, version)
            if key not in self._strategies:
                raise KeyError(f"{name}@{version} not found")
            return self._strategies[key].factory()
        # fallback to highest version (lexicographic)
        candidates = [sv for (n, _), sv in self._strategies.items() if n == name and sv.active]
        if not candidates:
            raise KeyError(f"No active strategy for {name}")
        chosen = sorted(candidates, key=lambda x: x.version)[-1]
        return chosen.factory()

    def list(self, active_only: bool = True) -> List[StrategyVersion]:
        return [sv for sv in self._strategies.values() if (sv.active or not active_only)]
