"""SDK boundary.

The only modules in the project that import `google.ads.googleads.*`. They
convert vendor protos to/from internal types (`google_ads_mcp.types`) so
everything above this layer stays vendor-free and testable without the SDK.

Files in this package use `# pyright: basic` because the SDK is untyped;
strict mode applies everywhere else.
"""
