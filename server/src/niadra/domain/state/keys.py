"""Names of the typed state the read path keeps in the cache store (06, 15.3).

An object's copy sits beside its version key, under one hash tag, so the store writes a copy only when its
version is not older than the newest one written (`put_if_generation`). The tag is a keyed digest of the
object's key: a system id never appears in a key name. The cache epoch sits outside the tag, as for the packs:
a failover bumps it and every old copy goes invisible.
"""

from uuid import UUID


def object_key(epoch: int, space_id: UUID, digest: str) -> str:
    return f"st:{epoch}:{space_id}:{{{digest}}}"


def object_version_key(epoch: int, space_id: UUID, digest: str) -> str:
    return f"stv:{epoch}:{space_id}:{{{digest}}}"


def relation_key(epoch: int, space_id: UUID, digest: str) -> str:
    """The objects a reverse relation finds by one value of one field, with the version they were read at."""
    return f"rl:{epoch}:{space_id}:{{{digest}}}"


def relation_version_key(epoch: int, space_id: UUID, digest: str) -> str:
    """Moved by every write of an object that holds the value: an index read before it is read again."""
    return f"rlv:{epoch}:{space_id}:{{{digest}}}"


def subject_objects_key(epoch: int, space_id: UUID, profile_id: UUID) -> str:
    """The typed objects of a customer, for the state block of a context read; a profile's purge takes it."""
    return f"sx:{epoch}:{space_id}:{{{profile_id}}}"
