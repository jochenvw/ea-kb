# 0002: Small async monolith, deterministic parsing first

**Decision.** Python 3.13, uv, bounded asyncio queues and stage semaphores. Network and database
I/O are asynchronous; CPU/blocking parser operations run in threads. Transport adapters share
application services and a composition root. No distributed agent execution or graph database.

PyMuPDF, python-docx and python-pptx provide deterministic structured extraction. OCR is opt-in and
only attempted for PDF pages lacking usable embedded text. The installed Tesseract language data
must be supplied by the operator. Images retain references into the content-addressed original.

Docling was assessed but is not a mandatory dependency: its docling-parse wheel metadata returned
404 through the configured package proxy during bootstrap. The format-specific parsers keep the
MVP reproducible without downloading large OCR/model weights. A future Docling parser can implement
the same document-parser port after dependency compatibility is resolved.

The default extraction provider is a clearly labeled conservative offline rules baseline.
OpenAI-compatible HTTP and optional Microsoft Agent Framework chat clients provide real structured
model extraction. Endpoints, capability flags and model identifiers are configured, never assumed.
No autonomous agents or tool loops. Microsoft Foundry / Foundry Local work through their configured
OpenAI-compatible endpoints; managed identity is supported by the HTTP adapter.

**Consequence.** Tests and evaluation need neither cloud credentials nor paid inference.
Offline results are not represented as model benchmarks. The framework integration is optional to
avoid imposing provider SDK dependencies on the domain or deterministic ingestion.
