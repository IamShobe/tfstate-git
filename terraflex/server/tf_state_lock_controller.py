import json
from dataclasses import dataclass

from terraflex.server.base_state_lock_provider import (
    Data,
    LockBody,
    LockingError,
    StateLockProviderProtocol,
)
from terraflex.server.storage_provider_base import (
    ItemKey,
    LockableStorageProviderProtocol,
    WriteableStorageProviderProtocol,
)
from terraflex.server.transformation_base import (
    TransformerProtocol,
)


@dataclass
class TFStack:
    name: str
    data_transformers: list[TransformerProtocol]
    storage_driver: WriteableStorageProviderProtocol
    state_file_storage_identifier: ItemKey


class TFStateLockController(StateLockProviderProtocol):
    def __init__(
        self,
        stacks: dict[str, TFStack],
    ):
        self.stacks = stacks

    def _validate_stack(self, stack_name: str) -> TFStack:
        stack = self.stacks.get(stack_name)
        if stack is None:
            raise ValueError(f"Undeclared stack: {stack_name}")

        return stack

    async def get(self, stack_name: str) -> Data | None:
        stack = self._validate_stack(stack_name)
        try:
            data = await stack.storage_driver.get_file(stack.state_file_storage_identifier)

        except FileNotFoundError:
            return None

        content = data
        for transformer in reversed(stack.data_transformers):
            content = await transformer.transform_read_file_content(
                stack.state_file_storage_identifier.as_string(), content
            )

        return json.loads(content)

    async def put(self, stack_name: str, lock_id: str, value: Data) -> None:
        stack = self._validate_stack(stack_name)
        await self._check_lock(stack_name, lock_id)
        # lock is locked by me

        data = json.dumps(value).encode()
        for transformer in stack.data_transformers:
            data = await transformer.transform_write_file_content(stack.state_file_storage_identifier.as_string(), data)

        await stack.storage_driver.put_file(stack.state_file_storage_identifier, data)

    async def delete(self, stack_name: str, lock_id: str) -> None:
        stack = self._validate_stack(stack_name)
        await self._check_lock(stack_name, lock_id)
        # lock is locked by me

        await stack.storage_driver.delete_file(stack.state_file_storage_identifier)

    async def read_lock(self, stack_name: str) -> LockBody | None:
        stack = self._validate_stack(stack_name)
        if not isinstance(stack.storage_driver, LockableStorageProviderProtocol):
            raise NotImplementedError("This storage provider does not support writing")

        try:
            return await stack.storage_driver.read_lock(stack.state_file_storage_identifier)

        except FileNotFoundError:
            return None

    async def _check_lock(self, stack_name: str, lock_id: str) -> LockBody:
        stack = self._validate_stack(stack_name)
        if not isinstance(stack.storage_driver, LockableStorageProviderProtocol):
            # This storage provider does not support locking
            return LockBody(
                ID="0000000000000000000", Operation="read", Who="me", Version="1", Created="2000-01-01T00:00:00Z"
            )

        data = await self.read_lock(stack_name)
        if data is None:
            raise LockingError(
                "Failed to lock state - no lock is present",
                lock_id=lock_id,
            )

        if data.ID != lock_id:
            raise LockingError(
                "Failed to lock state - someone else has already locked it",
                lock_id=lock_id,
            )

        return data

    async def lock(self, stack_name: str, data: LockBody) -> None:
        stack = self._validate_stack(stack_name)
        if not isinstance(stack.storage_driver, LockableStorageProviderProtocol):
            return

        await stack.storage_driver.acquire_lock(stack.state_file_storage_identifier, data)

    async def unlock(self, stack_name: str) -> None:
        stack = self._validate_stack(stack_name)
        if not isinstance(stack.storage_driver, LockableStorageProviderProtocol):
            return

        await stack.storage_driver.release_lock(stack.state_file_storage_identifier)
