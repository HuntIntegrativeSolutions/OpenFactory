"""OPC UA server (PLAN task 2.4, §6.3) on asyncua: the I/O table as OPC UA variables.

Behavior spec
-------------
:class:`OpcUaServer` serves one scene's I/O table at ``opc.tcp://BIND:PORT/openfactory/``
(``openfactory.toml`` ``[opcua]``, default ``127.0.0.1:4840``; PLAN §6.4: loopback unless the
operator opens it, anonymous, no security policy, a lab tool). The namespace is
``urn:openfactory:sim``. Under ``Objects`` there is one folder named after the scene, holding a
folder per component (browse name and string NodeId the component id) with one variable per
tag; the simulator's own ``SIM_HEARTBEAT_DI`` (PLAN §6.1) sits directly in the scene folder.

Every variable's NodeId is the tag name as a string, ``ns=<index>;s=CONV_01_RUN_DO``, so a
client finds the same nodes after a server restart (Ignition's OPC item path uses the namespace
URI, ``nsu=urn:openfactory:sim;s=CONV_01_RUN_DO``). ``bool`` tags are ``Boolean``, ``float32``
tags ``Float``, ``int16`` tags ``Int16``. DI and AI variables are read-only to clients (a write
gets ``BadNotWritable``); DO and AO variables are writable, and a client's write lands in the I/O
table as the PLC side (:attr:`~openfactory.core.iotable.Writer.PLC`), so a Modbus PLC and an OPC
UA client write the same tags with the same rules.

Values flow from the I/O table to the variables through :meth:`IOTable.on_change`: each change,
on whatever thread made it, is handed to the server's event loop with
``call_soon_threadsafe``, so subscribers see every change at the tick it happened, and nothing
runs under the simulation's step lock. On start every variable is set from the table once more
after the change subscription is in place, so no change is lost in between.

**Comms faults (PLAN 2.13b), as on the Modbus server.** :attr:`OpcUaServer.comms`
(:class:`~openfactory.protocols.comms.CommsFault`, one object shared with the Modbus server in a
run) stalls or delays the server without touching the simulation. ``drop``: every service
request (read, write, browse, publish, the client's keep-alive read) is ignored with no reply, so
the client's requests time out and a write in a dropped request is not applied; responses
already on their way, subscription notifications included, are not sent either. The request is
still decoded, so the secure channel's sequence numbers stay in step; once the fault clears the
server answers again (asyncua's own client closes a connection whose requests timed out and must
reconnect, as Ignition's client does by itself). Channel renewals (OpenSecureChannel) are still
answered.
``latency_ms``: each service request is processed that much later, and each subscription
notification is sent that much later. asyncua processes a connection's requests one after
another, so latency queues them, as a slow link does. Implemented by wrapping three
``UaProcessor`` methods of asyncua 2.1.0 (``server/uaprocessor.py``: ``process_message``,
``send_response``, ``forward_publish_response``), which look the fault up on their own server.

asyncua is LGPL-3.0-or-later, an optional extra (``openfactory[opcua]``) imported here only
(``THIRD_PARTY.md``). API from asyncua 2.1.0's own source (``server/server.py``,
``server/internal_session.py``: ``CallbackType.PostWrite`` carries the write request and its
per-node results). Not a safety-rated simulation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from asyncua import Server, ua
from asyncua.common.callback import CallbackType, ServerItemCallback
from asyncua.server.uaprocessor import UaProcessor

from openfactory.core.config import OPCUA_NAMESPACE, Endpoint
from openfactory.core.iotable import IOTable, Value, Writer
from openfactory.protocols.comms import CommsFault

log = logging.getLogger(__name__)

NAMESPACE = OPCUA_NAMESPACE
VARIANT = {
    "bool": ua.VariantType.Boolean,
    "float32": ua.VariantType.Float,
    "int16": ua.VariantType.Int16,
}
WRITABLE = frozenset({"DO", "AO"})
#: Attribute on asyncua's InternalServer holding the OpcUaServer that owns it.
_COMMS_ATTR = "openfactory_server"


def _comms_of(processor: UaProcessor) -> CommsFault | None:
    owner = getattr(processor.iserver, _COMMS_ATTR, None)
    return owner.comms if owner is not None else None


def _install_comms_hooks() -> None:
    """Wrap UaProcessor once so each connection obeys its server's comms fault (PLAN 2.13b)."""
    if getattr(UaProcessor, "_openfactory_comms_hooks", False):
        return
    process_message = UaProcessor.process_message
    send_response = UaProcessor.send_response
    forward_publish_response = UaProcessor.forward_publish_response

    async def _process_message(self: UaProcessor, seqhdr: Any, body: Any) -> Any:
        comms = _comms_of(self)
        if comms is not None:
            if comms.drop:
                return True  # decoded, never processed or answered: the client times out
            if comms.latency_ms > 0:
                await asyncio.sleep(comms.latency_ms / 1000.0)
                if comms.drop:
                    return True  # the fault became a drop while it waited
        return await process_message(self, seqhdr, body)

    def _send_response(
        self: UaProcessor,
        requesthandle: Any,
        seqhdr: Any,
        response: Any,
        msgtype: ua.MessageType = ua.MessageType.SecureMessage,
    ) -> None:
        comms = _comms_of(self)
        if comms is not None and comms.drop and msgtype == ua.MessageType.SecureMessage:
            return  # a reply already on its way when the fault started is lost too
        send_response(self, requesthandle, seqhdr, response, msgtype)

    async def _forward_publish_response(self: UaProcessor, result: Any, requestdata: Any) -> None:
        comms = _comms_of(self)
        if comms is not None and comms.latency_ms > 0:
            await asyncio.sleep(comms.latency_ms / 1000.0)  # a late value
        await forward_publish_response(self, result, requestdata)

    UaProcessor.process_message = _process_message  # type: ignore[method-assign]
    UaProcessor.send_response = _send_response  # type: ignore[method-assign]
    UaProcessor.forward_publish_response = _forward_publish_response  # type: ignore[method-assign]
    UaProcessor._openfactory_comms_hooks = True  # type: ignore[attr-defined]


class OpcUaServer:
    """Serve ``io`` over OPC UA; ``folders`` maps component id -> its description."""

    def __init__(
        self,
        io: IOTable,
        endpoint: Endpoint,
        *,
        scene_name: str,
        folders: dict[str, str] | None = None,
    ) -> None:
        self.io = io
        self.endpoint = endpoint
        self.scene_name = scene_name
        self.folders = dict(folders or {})
        self.namespace_index: int | None = None
        self._server: Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Any = None
        self._ready = False
        self._stop: asyncio.Event | None = None
        #: Comms fault (PLAN 2.13b), read on every request; the run shares one with Modbus.
        self.comms = CommsFault()

    @property
    def url(self) -> str:
        return f"opc.tcp://{self.endpoint.bind}:{self.endpoint.port}/openfactory/"

    def _nodeid(self, name: str) -> ua.NodeId:
        return ua.NodeId(name, self.namespace_index)

    async def _build(self) -> Server:
        _install_comms_hooks()
        server = Server()
        await server.init()
        setattr(server.iserver, _COMMS_ATTR, self)  # read through .comms: it may be replaced
        server.set_endpoint(self.url)
        server.set_server_name(f"OpenFactory: {self.scene_name}")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.namespace_index = await server.register_namespace(NAMESPACE)
        ns = self.namespace_index
        root = await server.nodes.objects.add_folder(
            ua.NodeId(f"scene:{self.scene_name}", ns), ua.QualifiedName(self.scene_name, ns)
        )
        folders = {}
        tags = self.io.tags()
        names = {t.name for t in tags}
        for owner in dict.fromkeys(t.owner for t in tags if t.owner):
            if owner in names:
                raise ValueError(f"component id {owner!r} is also a tag name; NodeIds would clash")
            folders[owner] = await root.add_folder(
                ua.NodeId(owner, ns), ua.QualifiedName(owner, ns)
            )
            if self.folders.get(owner):
                await folders[owner].write_attribute(
                    ua.AttributeIds.Description,
                    ua.DataValue(ua.Variant(ua.LocalizedText(self.folders[owner]))),
                )
        for tag in tags:
            parent = folders.get(tag.owner, root)
            var = await parent.add_variable(
                self._nodeid(tag.name),
                ua.QualifiedName(tag.name, ns),
                ua.Variant(tag.value, VARIANT[tag.dtype]),
            )
            if tag.description:
                await var.write_attribute(
                    ua.AttributeIds.Description,
                    ua.DataValue(ua.Variant(ua.LocalizedText(tag.description))),
                )
            if tag.kind in WRITABLE:
                await var.set_writable()
        server.subscribe_server_callback(CallbackType.PostWrite, self._post_write)
        return server

    # -- I/O table -> variables --------------------------------------------------------------------

    def _on_change(self, name: str, old: Value, new: Value, writer: Writer) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._publish, name, new)

    def _publish(self, name: str, value: Value) -> None:
        if self._server is not None:
            asyncio.ensure_future(self._write_value(name, value))

    async def _write_value(self, name: str, value: Value) -> None:
        server = self._server
        if server is None:
            return
        variant = ua.Variant(value, VARIANT[self.io.tag(name).dtype])
        try:
            await server.write_attribute_value(self._nodeid(name), ua.DataValue(variant))
        except Exception:  # noqa: BLE001 - a stopping server must not take the sim down
            log.debug("OPC UA: could not publish %s", name, exc_info=True)

    # -- client writes -> I/O table ----------------------------------------------------------------

    def _post_write(self, event: ServerItemCallback, _service: Any) -> None:
        if not event.is_external:
            return  # our own publishing
        for item, status in zip(
            event.request_params.NodesToWrite, event.response_params, strict=False
        ):
            nodeid = item.NodeId
            if not status.is_good() or nodeid.NamespaceIndex != self.namespace_index:
                continue
            if item.AttributeId != ua.AttributeIds.Value:
                continue
            name = nodeid.Identifier
            if name not in self.io or self.io.tag(name).kind not in WRITABLE:
                continue
            try:
                self.io.set(name, item.Value.Value.Value, Writer.PLC)
            except (TypeError, ValueError) as exc:
                log.warning("OPC UA: write to %s rejected: %s", name, exc)

    # -- lifecycle ---------------------------------------------------------------------------------

    async def serve_forever(self) -> None:
        """Bind ``self.endpoint`` and serve until :meth:`shutdown`."""
        self._loop = asyncio.get_running_loop()
        self._stop = stop = asyncio.Event()  # made here: each serve may run on a new loop
        server = await self._build()
        await server.start()
        self._server = server
        self._unsubscribe = self.io.on_change(self._on_change)
        for name, value in self.io.values().items():
            await self._write_value(name, value)
        log.info("OPC UA listening on %s (namespace %s)", self.url, NAMESPACE)
        self._ready = True
        try:
            await stop.wait()
        finally:
            await self._close()

    async def _close(self) -> None:
        self._ready = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        server, self._server = self._server, None
        if server is not None:
            await server.stop()

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._server is not None:
            await self._close()

    async def wait_ready(self, timeout: float = 15.0) -> None:
        """Wait until the server is listening (asyncua's init takes a second or two)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._ready:
            if loop.time() > deadline:
                raise TimeoutError(f"OPC UA server not listening on {self.url}")
            await asyncio.sleep(0.02)
