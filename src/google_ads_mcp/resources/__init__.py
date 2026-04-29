"""MCP resource registrations.

Resources are URIs the client fetches on demand; they don't count against
the ambient tool-token budget. Two of them in v1:

- `gads-account://accessible` — accessible customer IDs (CustomerService).
- `gads-schema://{resource_type}` — field metadata (GoogleAdsFieldService).

Both cache responses for the server lifetime since field metadata and the
accessible-account list change rarely.
"""
