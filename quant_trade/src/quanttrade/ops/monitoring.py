from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MetricsSink:
    enabled: bool = False
    endpoint: str | None = None

    def emit(self, name: str, value: float, tags: Dict[str, str] | None = None) -> None:
        if not self.enabled:
            return
        logger.info("metric %s=%s tags=%s endpoint=%s", name, value, tags, self.endpoint)
