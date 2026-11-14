"""Structured audit logging for Free Point harness."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class AuditLogger:
    """Structured JSON audit logger with taint tracking."""

    def __init__(self, logger_name: str = "free_point_audit") -> None:
        self.logger = logging.getLogger(logger_name)
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
            self.logger.propagate = False

    def log_event(
        self,
        event_type: str,
        job_id: Optional[str] = None,
        request_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        tainted_by_untrusted_source: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Emit structured JSON log event for compliance and traceability."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "job_id": job_id,
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result,
            "tainted_by_untrusted_source": tainted_by_untrusted_source,
            "details": details or {},
        }
        self.logger.info(json.dumps(entry, default=str))
        return entry


audit_logger = AuditLogger()
