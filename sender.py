"""私聊发送端口：把文本交给宿主适配器，并把失败统一成 DeliveryError。"""

from __future__ import annotations

from typing import Any

from .errors import DeliveryError


class PrivateMessageSender:
    """按 QQ 号私聊发送；不依赖 Reply，可在生命周期后台任务中使用。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def send_text(self, destination_id: int, text: str) -> None:
        if not text:
            return
        send_private = getattr(self._ctx, "send_private", None)
        if callable(send_private):
            response = await send_private(int(destination_id), text)
        else:
            adapter = getattr(self._ctx, "adapter", None)
            method = getattr(adapter, "send_private_msg", None)
            if not callable(method):
                raise DeliveryError("宿主适配器不支持私聊发送")
            response = await method(int(destination_id), text)
        _raise_for_response(response)


def _raise_for_response(response: Any) -> None:
    if response is None:
        return
    if isinstance(response, dict):
        status = response.get("status")
        retcode = response.get("retcode")
    else:
        status = getattr(response, "status", None)
        retcode = getattr(response, "retcode", None)
    status_text = str(status).strip().lower() if status is not None else None
    okay_status = status_text is None or status_text in {"ok", "async", "success"}
    okay_retcode = retcode is None or str(retcode) in {"0", "1"}
    if not okay_status or not okay_retcode:
        raise DeliveryError("适配器返回发送失败状态")


__all__ = ["PrivateMessageSender"]
