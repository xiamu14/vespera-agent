"""Pause a failed model request until the user retries or stops the run."""
import asyncio
from uuid import uuid4

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelAPIError, RetryModelRequest


def model_error_text(error: ModelAPIError) -> str:
    status = getattr(error, "status_code", None)
    message = f"模型请求失败（HTTP {status}）。" if type(status) is int else "模型请求失败。"
    if status in (401, 403):
        message += "请检查模型服务的配置和访问权限。"
    return message


class ModelRequestRetry(AbstractCapability):
    def __init__(self, emit):
        self.emit = emit
        self.pending = None

    def confirm(self, token):
        if self.pending is None:
            return False
        expected, future = self.pending
        if token != expected or future.done():
            return False
        future.set_result(None)
        return True

    async def on_model_request_error(self, ctx, *, request_context, error):
        if not isinstance(error, ModelAPIError):
            raise error
        token = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending = token, future
        self.emit({"type": "model_failure", "token": token, "message": model_error_text(error)})
        try:
            await future
        finally:
            self.pending = None
        self.emit({"type": "model_retrying", "token": token})
        raise RetryModelRequest()
