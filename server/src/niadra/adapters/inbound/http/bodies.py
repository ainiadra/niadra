"""Request bodies compressed by the sender: gzip, and zstd for turn records (24.3.5).

A body is refused before it is read when it declares a length over the compressed limit, and decompression
stops one byte past the decompressed limit, so a small body that inflates never fills memory.

A large body, one over `LargeBodies.threshold` sent or decoded, also takes one of the process's slots until
its request ends. A decoded 16 MB body and what is parsed from it hold 30 to 55 MiB of the process, and four
at once took an ingest pod past its limit (29/09/2026): without a free slot the request is refused with 429
and `Retry-After`, and the SDKs send it again.
"""

import zlib
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

import zstandard
from fastapi import Depends, Request

from niadra.adapters.inbound.http.deps import service
from niadra.domain.common.errors import InvalidInput, NiadraError, RateLimited

_CHUNK = 64 * 1024
RETRY_AFTER_S = 2


class LargeBodies:
    """At most `slots` large bodies of this process are held at once."""

    def __init__(self, slots: int, threshold: int) -> None:
        self.slots = slots
        self.threshold = threshold
        self.held = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self.held >= self.slots:
            raise RateLimited(RETRY_AFTER_S, f"more than {self.slots} large bodies at once; send it again")
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1


Large = Annotated[LargeBodies, Depends(service("large_bodies"))]


@asynccontextmanager
async def decoded_body(
    request: Request,
    large: LargeBodies,
    compressed_limit: int,
    decoded_limit: int,
    too_large: type[NiadraError] = InvalidInput,
) -> AsyncIterator[bytes]:
    """The decoded body, for the block; a large one holds its slot until the block ends."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > compressed_limit:
        raise too_large(f"the body exceeds {compressed_limit} bytes")
    body = await request.body()
    if len(body) > compressed_limit:
        raise too_large(f"the body exceeds {compressed_limit} bytes")
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    async with AsyncExitStack() as stack:
        if len(body) > large.threshold:
            await stack.enter_async_context(large.slot())
        if encoding in ("", "identity"):
            yield body
            return
        read = _reader(encoding, body)
        try:
            out = read(large.threshold + 1)
            if large.threshold < len(out) <= decoded_limit:
                # Past the threshold only now: a small body that inflates takes its slot before it grows.
                if len(body) <= large.threshold:
                    await stack.enter_async_context(large.slot())
                out += read(decoded_limit + 1 - len(out))
        except (zlib.error, zstandard.ZstdError):
            raise InvalidInput("the body does not decompress") from None
        if len(out) > decoded_limit:
            raise too_large(f"the body exceeds {decoded_limit} bytes once decompressed")
        yield out


def _reader(encoding: str, body: bytes) -> Callable[[int], bytes]:
    """Decodes the body a part at a time: each call returns up to `most` more bytes, fewer at its end."""
    if encoding == "gzip":
        decoder = zlib.decompressobj(wbits=31)
        pending = body

        def gunzip(most: int) -> bytes:
            nonlocal pending
            out = decoder.decompress(pending, most)
            pending = decoder.unconsumed_tail
            return out

        return gunzip
    if encoding == "zstd":
        reader = zstandard.ZstdDecompressor().stream_reader(body)

        def unzstd(most: int) -> bytes:
            out = bytearray()
            while len(out) < most and (chunk := reader.read(min(_CHUNK, most - len(out)))):
                out += chunk
            return bytes(out)

        return unzstd
    raise InvalidInput("send the body as gzip, zstd or uncompressed")
