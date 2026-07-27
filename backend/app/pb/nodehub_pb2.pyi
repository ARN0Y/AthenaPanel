from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentMessage(_message.Message):
    __slots__ = ("hello", "report")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    REPORT_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    report: Report
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., report: _Optional[_Union[Report, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("token", "agent_version", "protocol_version", "hostname", "os", "kernel")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    AGENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    HOSTNAME_FIELD_NUMBER: _ClassVar[int]
    OS_FIELD_NUMBER: _ClassVar[int]
    KERNEL_FIELD_NUMBER: _ClassVar[int]
    token: str
    agent_version: str
    protocol_version: int
    hostname: str
    os: str
    kernel: str
    def __init__(self, token: _Optional[str] = ..., agent_version: _Optional[str] = ..., protocol_version: _Optional[int] = ..., hostname: _Optional[str] = ..., os: _Optional[str] = ..., kernel: _Optional[str] = ...) -> None: ...

class Report(_message.Message):
    __slots__ = ("sent_at_unix_ms", "host", "ppp", "wg")
    SENT_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PPP_FIELD_NUMBER: _ClassVar[int]
    WG_FIELD_NUMBER: _ClassVar[int]
    sent_at_unix_ms: int
    host: Host
    ppp: _containers.RepeatedCompositeFieldContainer[PppSession]
    wg: _containers.RepeatedCompositeFieldContainer[WgPeer]
    def __init__(self, sent_at_unix_ms: _Optional[int] = ..., host: _Optional[_Union[Host, _Mapping]] = ..., ppp: _Optional[_Iterable[_Union[PppSession, _Mapping]]] = ..., wg: _Optional[_Iterable[_Union[WgPeer, _Mapping]]] = ...) -> None: ...

class Host(_message.Message):
    __slots__ = ("uptime_seconds", "load1", "mem_total_bytes", "mem_available_bytes", "xl2tpd_ok", "ipsec_ok", "accel_ppp_ok", "wireguard_ok")
    UPTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    LOAD1_FIELD_NUMBER: _ClassVar[int]
    MEM_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEM_AVAILABLE_BYTES_FIELD_NUMBER: _ClassVar[int]
    XL2TPD_OK_FIELD_NUMBER: _ClassVar[int]
    IPSEC_OK_FIELD_NUMBER: _ClassVar[int]
    ACCEL_PPP_OK_FIELD_NUMBER: _ClassVar[int]
    WIREGUARD_OK_FIELD_NUMBER: _ClassVar[int]
    uptime_seconds: int
    load1: float
    mem_total_bytes: int
    mem_available_bytes: int
    xl2tpd_ok: bool
    ipsec_ok: bool
    accel_ppp_ok: bool
    wireguard_ok: bool
    def __init__(self, uptime_seconds: _Optional[int] = ..., load1: _Optional[float] = ..., mem_total_bytes: _Optional[int] = ..., mem_available_bytes: _Optional[int] = ..., xl2tpd_ok: bool = ..., ipsec_ok: bool = ..., accel_ppp_ok: bool = ..., wireguard_ok: bool = ...) -> None: ...

class PppSession(_message.Message):
    __slots__ = ("ifname", "rx_bytes", "tx_bytes", "username", "peer_ip", "pid")
    IFNAME_FIELD_NUMBER: _ClassVar[int]
    RX_BYTES_FIELD_NUMBER: _ClassVar[int]
    TX_BYTES_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PEER_IP_FIELD_NUMBER: _ClassVar[int]
    PID_FIELD_NUMBER: _ClassVar[int]
    ifname: str
    rx_bytes: int
    tx_bytes: int
    username: str
    peer_ip: str
    pid: int
    def __init__(self, ifname: _Optional[str] = ..., rx_bytes: _Optional[int] = ..., tx_bytes: _Optional[int] = ..., username: _Optional[str] = ..., peer_ip: _Optional[str] = ..., pid: _Optional[int] = ...) -> None: ...

class WgPeer(_message.Message):
    __slots__ = ("public_key", "rx_bytes", "tx_bytes", "last_handshake_unix", "address")
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    RX_BYTES_FIELD_NUMBER: _ClassVar[int]
    TX_BYTES_FIELD_NUMBER: _ClassVar[int]
    LAST_HANDSHAKE_UNIX_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    public_key: str
    rx_bytes: int
    tx_bytes: int
    last_handshake_unix: int
    address: str
    def __init__(self, public_key: _Optional[str] = ..., rx_bytes: _Optional[int] = ..., tx_bytes: _Optional[int] = ..., last_handshake_unix: _Optional[int] = ..., address: _Optional[str] = ...) -> None: ...

class HubMessage(_message.Message):
    __slots__ = ("welcome", "ack")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    ack: Ack
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("node_id", "node_name", "report_interval_seconds", "protocol_version")
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    NODE_NAME_FIELD_NUMBER: _ClassVar[int]
    REPORT_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    node_id: int
    node_name: str
    report_interval_seconds: int
    protocol_version: int
    def __init__(self, node_id: _Optional[int] = ..., node_name: _Optional[str] = ..., report_interval_seconds: _Optional[int] = ..., protocol_version: _Optional[int] = ...) -> None: ...

class Ack(_message.Message):
    __slots__ = ("received_at_unix_ms", "ok", "detail")
    RECEIVED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    received_at_unix_ms: int
    ok: bool
    detail: str
    def __init__(self, received_at_unix_ms: _Optional[int] = ..., ok: bool = ..., detail: _Optional[str] = ...) -> None: ...
