from typing import Protocol


class BlobStore(Protocol):
    """Port: where uploads are kept. Adapters live beside the services that use them."""

    def put(self, key: str, data: bytes) -> None: ...
