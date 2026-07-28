from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentMessage(_message.Message):
    __slots__ = ("hello", "report", "credit_request", "sync_ack")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    REPORT_FIELD_NUMBER: _ClassVar[int]
    CREDIT_REQUEST_FIELD_NUMBER: _ClassVar[int]
    SYNC_ACK_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    report: Report
    credit_request: CreditRequest
    sync_ack: SyncAck
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., report: _Optional[_Union[Report, _Mapping]] = ..., credit_request: _Optional[_Union[CreditRequest, _Mapping]] = ..., sync_ack: _Optional[_Union[SyncAck, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("token", "agent_version", "protocol_version", "hostname", "os", "kernel", "has_l2tp", "has_sstp", "has_wireguard")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    AGENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    HOSTNAME_FIELD_NUMBER: _ClassVar[int]
    OS_FIELD_NUMBER: _ClassVar[int]
    KERNEL_FIELD_NUMBER: _ClassVar[int]
    HAS_L2TP_FIELD_NUMBER: _ClassVar[int]
    HAS_SSTP_FIELD_NUMBER: _ClassVar[int]
    HAS_WIREGUARD_FIELD_NUMBER: _ClassVar[int]
    token: str
    agent_version: str
    protocol_version: int
    hostname: str
    os: str
    kernel: str
    has_l2tp: bool
    has_sstp: bool
    has_wireguard: bool
    def __init__(self, token: _Optional[str] = ..., agent_version: _Optional[str] = ..., protocol_version: _Optional[int] = ..., hostname: _Optional[str] = ..., os: _Optional[str] = ..., kernel: _Optional[str] = ..., has_l2tp: bool = ..., has_sstp: bool = ..., has_wireguard: bool = ...) -> None: ...

class Report(_message.Message):
    __slots__ = ("sent_at_unix_ms", "host", "ppp", "wg", "ppp_scan_failed")
    SENT_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PPP_FIELD_NUMBER: _ClassVar[int]
    WG_FIELD_NUMBER: _ClassVar[int]
    PPP_SCAN_FAILED_FIELD_NUMBER: _ClassVar[int]
    sent_at_unix_ms: int
    host: Host
    ppp: _containers.RepeatedCompositeFieldContainer[PppSession]
    wg: _containers.RepeatedCompositeFieldContainer[WgPeer]
    ppp_scan_failed: bool
    def __init__(self, sent_at_unix_ms: _Optional[int] = ..., host: _Optional[_Union[Host, _Mapping]] = ..., ppp: _Optional[_Iterable[_Union[PppSession, _Mapping]]] = ..., wg: _Optional[_Iterable[_Union[WgPeer, _Mapping]]] = ..., ppp_scan_failed: bool = ...) -> None: ...

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
    __slots__ = ("ifname", "rx_bytes", "tx_bytes", "username", "peer_ip", "pid", "started_at_unix")
    IFNAME_FIELD_NUMBER: _ClassVar[int]
    RX_BYTES_FIELD_NUMBER: _ClassVar[int]
    TX_BYTES_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PEER_IP_FIELD_NUMBER: _ClassVar[int]
    PID_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    ifname: str
    rx_bytes: int
    tx_bytes: int
    username: str
    peer_ip: str
    pid: int
    started_at_unix: int
    def __init__(self, ifname: _Optional[str] = ..., rx_bytes: _Optional[int] = ..., tx_bytes: _Optional[int] = ..., username: _Optional[str] = ..., peer_ip: _Optional[str] = ..., pid: _Optional[int] = ..., started_at_unix: _Optional[int] = ...) -> None: ...

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

class CreditRequest(_message.Message):
    __slots__ = ("username", "reason", "consumed_bytes", "grant_id", "session_rx_bytes", "session_tx_bytes", "ifname")
    class Reason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        INITIAL: _ClassVar[CreditRequest.Reason]
        THRESHOLD: _ClassVar[CreditRequest.Reason]
        VALIDITY: _ClassVar[CreditRequest.Reason]
        SESSION_ENDED: _ClassVar[CreditRequest.Reason]
        EXHAUSTED: _ClassVar[CreditRequest.Reason]
    INITIAL: CreditRequest.Reason
    THRESHOLD: CreditRequest.Reason
    VALIDITY: CreditRequest.Reason
    SESSION_ENDED: CreditRequest.Reason
    EXHAUSTED: CreditRequest.Reason
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CONSUMED_BYTES_FIELD_NUMBER: _ClassVar[int]
    GRANT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_RX_BYTES_FIELD_NUMBER: _ClassVar[int]
    SESSION_TX_BYTES_FIELD_NUMBER: _ClassVar[int]
    IFNAME_FIELD_NUMBER: _ClassVar[int]
    username: str
    reason: CreditRequest.Reason
    consumed_bytes: int
    grant_id: int
    session_rx_bytes: int
    session_tx_bytes: int
    ifname: str
    def __init__(self, username: _Optional[str] = ..., reason: _Optional[_Union[CreditRequest.Reason, str]] = ..., consumed_bytes: _Optional[int] = ..., grant_id: _Optional[int] = ..., session_rx_bytes: _Optional[int] = ..., session_tx_bytes: _Optional[int] = ..., ifname: _Optional[str] = ...) -> None: ...

class SyncAck(_message.Message):
    __slots__ = ("sync_id", "ok", "detail", "users_applied")
    SYNC_ID_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    USERS_APPLIED_FIELD_NUMBER: _ClassVar[int]
    sync_id: int
    ok: bool
    detail: str
    users_applied: int
    def __init__(self, sync_id: _Optional[int] = ..., ok: bool = ..., detail: _Optional[str] = ..., users_applied: _Optional[int] = ...) -> None: ...

class HubMessage(_message.Message):
    __slots__ = ("welcome", "ack", "credit_grant", "user_sync", "disconnect")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    CREDIT_GRANT_FIELD_NUMBER: _ClassVar[int]
    USER_SYNC_FIELD_NUMBER: _ClassVar[int]
    DISCONNECT_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    ack: Ack
    credit_grant: CreditGrant
    user_sync: UserSync
    disconnect: Disconnect
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ..., credit_grant: _Optional[_Union[CreditGrant, _Mapping]] = ..., user_sync: _Optional[_Union[UserSync, _Mapping]] = ..., disconnect: _Optional[_Union[Disconnect, _Mapping]] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("node_id", "node_name", "report_interval_seconds", "protocol_version", "credit_poll_ms")
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    NODE_NAME_FIELD_NUMBER: _ClassVar[int]
    REPORT_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    CREDIT_POLL_MS_FIELD_NUMBER: _ClassVar[int]
    node_id: int
    node_name: str
    report_interval_seconds: int
    protocol_version: int
    credit_poll_ms: int
    def __init__(self, node_id: _Optional[int] = ..., node_name: _Optional[str] = ..., report_interval_seconds: _Optional[int] = ..., protocol_version: _Optional[int] = ..., credit_poll_ms: _Optional[int] = ...) -> None: ...

class Ack(_message.Message):
    __slots__ = ("received_at_unix_ms", "ok", "detail")
    RECEIVED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    received_at_unix_ms: int
    ok: bool
    detail: str
    def __init__(self, received_at_unix_ms: _Optional[int] = ..., ok: bool = ..., detail: _Optional[str] = ...) -> None: ...

class CreditGrant(_message.Message):
    __slots__ = ("username", "grant_id", "granted_bytes", "threshold_bytes", "validity_seconds", "final", "on_hub_unreachable", "refused", "refuse_reason")
    class FailureAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        CONTINUE_THEN_TERMINATE: _ClassVar[CreditGrant.FailureAction]
        TERMINATE_IMMEDIATELY: _ClassVar[CreditGrant.FailureAction]
    CONTINUE_THEN_TERMINATE: CreditGrant.FailureAction
    TERMINATE_IMMEDIATELY: CreditGrant.FailureAction
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    GRANT_ID_FIELD_NUMBER: _ClassVar[int]
    GRANTED_BYTES_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_BYTES_FIELD_NUMBER: _ClassVar[int]
    VALIDITY_SECONDS_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    ON_HUB_UNREACHABLE_FIELD_NUMBER: _ClassVar[int]
    REFUSED_FIELD_NUMBER: _ClassVar[int]
    REFUSE_REASON_FIELD_NUMBER: _ClassVar[int]
    username: str
    grant_id: int
    granted_bytes: int
    threshold_bytes: int
    validity_seconds: int
    final: bool
    on_hub_unreachable: CreditGrant.FailureAction
    refused: bool
    refuse_reason: str
    def __init__(self, username: _Optional[str] = ..., grant_id: _Optional[int] = ..., granted_bytes: _Optional[int] = ..., threshold_bytes: _Optional[int] = ..., validity_seconds: _Optional[int] = ..., final: bool = ..., on_hub_unreachable: _Optional[_Union[CreditGrant.FailureAction, str]] = ..., refused: bool = ..., refuse_reason: _Optional[str] = ...) -> None: ...

class UserSync(_message.Message):
    __slots__ = ("sync_id", "users", "full")
    class Entry(_message.Message):
        __slots__ = ("username", "password", "enabled", "rate_down_kbps", "rate_up_kbps", "l2tp_mode", "outbound")
        USERNAME_FIELD_NUMBER: _ClassVar[int]
        PASSWORD_FIELD_NUMBER: _ClassVar[int]
        ENABLED_FIELD_NUMBER: _ClassVar[int]
        RATE_DOWN_KBPS_FIELD_NUMBER: _ClassVar[int]
        RATE_UP_KBPS_FIELD_NUMBER: _ClassVar[int]
        L2TP_MODE_FIELD_NUMBER: _ClassVar[int]
        OUTBOUND_FIELD_NUMBER: _ClassVar[int]
        username: str
        password: str
        enabled: bool
        rate_down_kbps: int
        rate_up_kbps: int
        l2tp_mode: str
        outbound: str
        def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ..., enabled: bool = ..., rate_down_kbps: _Optional[int] = ..., rate_up_kbps: _Optional[int] = ..., l2tp_mode: _Optional[str] = ..., outbound: _Optional[str] = ...) -> None: ...
    SYNC_ID_FIELD_NUMBER: _ClassVar[int]
    USERS_FIELD_NUMBER: _ClassVar[int]
    FULL_FIELD_NUMBER: _ClassVar[int]
    sync_id: int
    users: _containers.RepeatedCompositeFieldContainer[UserSync.Entry]
    full: bool
    def __init__(self, sync_id: _Optional[int] = ..., users: _Optional[_Iterable[_Union[UserSync.Entry, _Mapping]]] = ..., full: bool = ...) -> None: ...

class Disconnect(_message.Message):
    __slots__ = ("username", "reason")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    username: str
    reason: str
    def __init__(self, username: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...
