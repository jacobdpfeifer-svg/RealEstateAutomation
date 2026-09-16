from __future__ import annotations

from adapters.base import SkipTraceProvider

from providers.skip_trace.stub import StubSkipTraceProvider


def get_skip_trace_provider(name: str = "stub") -> SkipTraceProvider:
    """
    Resolve a skip-trace provider by name.

    `batchdata` is imported lazily so the package stays importable without an API key.
    If batchdata is requested but BATCHDATA_API_KEY is missing, fall back to stub
    with a stderr warning so enrich still completes for manual paste.
    """
    if name == "stub":
        return StubSkipTraceProvider()
    if name == "batchdata":
        try:
            from providers.skip_trace.batchdata import BatchDataSkipTraceProvider

            return BatchDataSkipTraceProvider()
        except RuntimeError as exc:
            import sys

            print(f"skip_trace: batchdata unavailable ({exc}); falling back to stub", file=sys.stderr)
            return StubSkipTraceProvider()
    raise KeyError(f"Unknown skip trace provider: {name}. Available: stub, batchdata")
