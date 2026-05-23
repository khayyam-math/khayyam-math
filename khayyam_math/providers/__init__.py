"""Provider implementations.

Each provider exposes a single method::

    .complete(system: str, user: str, max_tokens: int,
              temperature: float) -> str

returning the raw model text.  The shared :class:`khayyam_math.client.KhayyamMath`
class handles JSON parsing and result normalisation, so the providers
themselves stay small and focused on transport.
"""
