"""Authentication layer.

`CredentialsProvider` is the seam: anything above this module asks for
credentials through it and never touches storage. v1 has one impl
(`LocalRefreshTokenCredentials`); v2 can swap in an OAuth-proxy or
service-account impl without changes upstream.
"""
