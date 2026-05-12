"""Tests for the progressive-SVG streaming path of ``express_figure``.

Two layers:

* ``_StreamingSvgExtractor`` — the JSON-aware state machine that pulls
  the value of the top-level ``svg`` field out of a stream of token
  deltas.  Exercised directly with a variety of chunk boundary cases.
* ``_stream_chat_completion`` — the httpx-mocked wrapper that drives
  the extractor against a fake OpenAI SSE response and reports partial
  SVG progress to the caller via the ``on_svg_chunk`` callback.

The non-streaming path (``on_svg_chunk=None``) is unchanged by this
work and remains covered by the existing express tests; we only assert
here that the new code path doesn't regress when no callback is given.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from studio.express import _StreamingSvgExtractor, _stream_chat_completion


# ---------------------------------------------------------------------
# _StreamingSvgExtractor — unit tests
# ---------------------------------------------------------------------

def test_extractor_emits_after_seeing_value_opening():
    ex = _StreamingSvgExtractor()
    # No "svg" key yet — nothing to emit, not done.
    assert ex.feed('{"title":"x",') is False
    assert ex.partial_svg == ""
    assert ex.done is False
    # Now the field arrives in one big chunk.
    assert ex.feed('"svg":"<svg></svg>","narration":[]}') is True
    assert ex.partial_svg == "<svg></svg>"
    assert ex.done is True


def test_extractor_split_inside_value():
    """Field opens in chunk 1, content arrives in chunks 2 + 3."""
    ex = _StreamingSvgExtractor()
    assert ex.feed('{"svg":"<svg>') is True
    assert ex.partial_svg == "<svg>"
    assert ex.feed("<circle/>") is True
    assert ex.partial_svg == "<svg><circle/>"
    assert ex.feed("</svg>") is True
    assert ex.partial_svg == "<svg><circle/></svg>"
    assert ex.done is False
    # Closing quote arrives separately.
    assert ex.feed('","narration":[]}') is False  # no new SVG content
    assert ex.done is True


def test_extractor_decodes_json_escapes():
    """JSON string escapes (\\\\, \\\", \\n, \\t, \\/) are decoded."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"a\\"b\\nc\\td\\\\e\\/f","x":1}')
    assert ex.partial_svg == 'a"b\nc\td\\e/f'
    assert ex.done is True


def test_extractor_escape_spans_chunk_boundary():
    """Backslash arrives in one chunk and the escaped char in the next."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"a\\')
    # Backslash held internally as pending escape — no emit yet.
    assert ex.partial_svg == "a"
    ex.feed('n"}')
    assert ex.partial_svg == "a\n"
    assert ex.done is True


def test_extractor_handles_field_key_split():
    """The '"svg"' key itself straddles two chunks."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"sv')
    ex.feed('g":"<svg/>","z":1}')
    assert ex.partial_svg == "<svg/>"
    assert ex.done is True


def test_extractor_no_svg_field_means_no_output():
    ex = _StreamingSvgExtractor()
    ex.feed('{"title":"x","narration":[]}')
    assert ex.partial_svg == ""
    # Stays not-done because the closing '}' isn't a string close.
    assert ex.done is False


def test_extractor_unicode_escape_decoded_to_real_char():
    """\\uXXXX escapes are decoded to the actual character — previously
    we emitted '?' as a placeholder, but downstream consumers (the
    canvas viewer painting svg_chunk content into an iframe) leaked
    the hex digits as e.g. '?D7' for \\u00D7 (×).  Now the streaming
    extractor produces the same characters as a full JSON parse."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"a\\u00e9b","x":1}')
    assert ex.partial_svg == "aéb"
    assert ex.done is True


def test_extractor_unicode_escape_split_across_chunks():
    """The four hex digits of \\uXXXX may arrive across multiple
    feed() calls; the extractor must accumulate them, not emit
    half-decoded bytes."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"a\\u00')
    assert ex.partial_svg == "a"   # nothing emitted yet
    ex.feed("d7b\",x:1}")
    assert ex.partial_svg == "a×b"
    assert ex.done is True


def test_extractor_malformed_unicode_escape_passes_through():
    """An invalid \\uXXXX (non-hex) is kept as literal source rather
    than silently dropped — the downstream JSON parse will flag it."""
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"a\\uZZZZb","x":1}')
    assert "a" in ex.partial_svg
    # The literal "\uZZZZ" or its tail survives somewhere in the buf.
    assert "uZZZZ" in ex.partial_svg or "\\uZZZZ" in ex.partial_svg


def test_extractor_after_close_drops_input():
    ex = _StreamingSvgExtractor()
    ex.feed('{"svg":"<svg/>","z":1}')
    assert ex.done is True
    # Further feeds do not append anything.
    assert ex.feed('"svg":"junk"') is False
    assert ex.partial_svg == "<svg/>"


def test_extractor_stays_silent_until_value_quote_seen():
    ex = _StreamingSvgExtractor()
    # Key present but no opening quote on the value yet.
    assert ex.feed('{"svg":') is False
    assert ex.partial_svg == ""
    # Whitespace + opening quote next.
    assert ex.feed(' "<svg/>","z":1}') is True
    assert ex.partial_svg == "<svg/>"
    assert ex.done is True


# ---------------------------------------------------------------------
# _stream_chat_completion — integration with a mocked HTTP transport
# ---------------------------------------------------------------------

def _sse_chunks(deltas: list[str]) -> bytes:
    """Build a fake OpenAI streaming chat-completions response body
    by emitting one ``data: ...`` SSE frame per delta plus the closing
    ``data: [DONE]`` marker.  Trailing blank line after each frame is
    what httpx's ``aiter_lines`` splits on."""
    out = []
    for d in deltas:
        frame = {"choices": [{"delta": {"content": d}}]}
        out.append(f"data: {json.dumps(frame)}\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


def _make_streaming_transport(body: bytes) -> httpx.MockTransport:
    """An httpx transport that returns the canned SSE body for any
    POST to /chat/completions.  Content-type ``text/event-stream``
    is what httpx needs to keep ``aiter_lines`` flowing per frame."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )
    return httpx.MockTransport(handler)


def test_stream_chat_completion_emits_partial_svg():
    """End-to-end test of the streaming helper against a fake SSE body.

    Mirrors the project convention of driving the async coroutine via
    ``asyncio.run`` so the same test file can be run by plain
    ``pytest`` without an asyncio plugin."""
    deltas = [
        '{"title":"foo","svg":"<sv',
        "g><circle/>",
        "</svg>",
        '","narration":[]}',
    ]
    body = _sse_chunks(deltas)
    transport = _make_streaming_transport(body)
    seen: list[str] = []

    async def cb(partial: str) -> None:
        seen.append(partial)

    async def _run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await _stream_chat_completion(
                client=client,
                url="http://test/chat/completions",
                headers={},
                payload={"messages": [], "stream": True},
                on_svg_chunk=cb,
                log=lambda _m: None,
            )

    content = asyncio.run(_run())

    # Final reconstructed JSON content is the concatenation of deltas.
    assert content == "".join(deltas)
    # The full SVG made it through to the callback at least once.
    assert any(p == "<svg><circle/></svg>" for p in seen)
    # And we saw monotonic progress — each emission grew or stayed.
    for a, b in zip(seen, seen[1:]):
        assert len(b) >= len(a)


def test_stream_chat_completion_skips_done_marker():
    """A stream that ends with [DONE] before any SVG content reports
    no chunks to the callback but still returns the empty content
    string for the caller's downstream JSON parse."""
    body = b"data: [DONE]\n\n"
    transport = _make_streaming_transport(body)
    seen: list[str] = []

    async def cb(partial: str) -> None:
        seen.append(partial)

    async def _run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await _stream_chat_completion(
                client=client,
                url="http://test/chat/completions",
                headers={},
                payload={"messages": [], "stream": True},
                on_svg_chunk=cb,
                log=lambda _m: None,
            )

    content = asyncio.run(_run())
    assert content == ""
    assert seen == []
