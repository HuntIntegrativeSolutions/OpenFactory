"""PLAN 3.5: the Sparkplug B payloads and MQTT packets the edge node sends, checked against bytes
worked out by hand from the specifications (sparkplug_b.proto's field numbers, the protobuf wire
format, MQTT 3.1.1 §3.1), not against this module's own decoder."""

from __future__ import annotations

import pytest

from openfactory.core.iotable import IOTable, Tag, Writer
from openfactory.protocols import sparkplug as sp


def test_a_payload_matches_bytes_worked_out_from_the_proto():
    metrics = [
        sp.encode_metric(name="A_DI", alias=1, datatype=sp.BOOLEAN, value=True),
        sp.encode_metric(name="B_AI", alias=2, datatype=sp.FLOAT, value=1.5),
    ]
    got = sp.encode_payload(1000, metrics, 0)
    a = bytes.fromhex("0a04" + b"A_DI".hex() + "1001" + "200b" + "7001")  # name, alias, Boolean
    # Float is fixed32: field 12, wire type 5 -> key 0x65; 1.5f little-endian is 00 00 c0 3f
    b = bytes.fromhex("0a04" + b"B_AI".hex() + "1002" + "2009" + "650000c03f")
    want = bytes.fromhex("08e807") + b"\x12" + bytes([len(a)]) + a
    want += b"\x12" + bytes([len(b)]) + b + bytes.fromhex("1800")  # seq 0
    assert got == want


def test_bdseq_is_an_int64_long_value_and_ndeath_has_no_seq():
    metric = sp.encode_metric(name="bdSeq", datatype=sp.INT64, value=3)
    assert metric == bytes.fromhex("0a05" + b"bdSeq".hex() + "2004" + "5803")  # field 11: 0x58
    death = sp.encode_payload(1, [metric], None)
    assert death == bytes.fromhex("0801") + b"\x12" + bytes([len(metric)]) + metric


def test_the_connect_packet_asks_for_a_qos1_will_and_a_clean_session():
    packet = sp.connect_packet("node", 30, "spBv1.0/G/NDEATH/N", b"\x08\x01")
    # fixed header 0x10; then "MQTT", level 4, flags 0x0E (clean session, Will, Will QoS 1,
    # Will retain 0), keep-alive 30
    variable = bytes.fromhex("00044d515454" + "04" + "0e" + "001e")
    payload = b"\x00\x04node" + b"\x00\x12spBv1.0/G/NDEATH/N" + b"\x00\x02\x08\x01"
    assert packet == bytes([0x10, len(variable) + len(payload)]) + variable + payload


def test_subscribe_and_publish_packets():
    assert sp.subscribe_packet(7, "a/b") == bytes.fromhex("820800070003612f6201")
    assert sp.publish_packet("t", b"x") == bytes.fromhex("3004" + "000174" + "78")
    assert sp.publish_packet("t", b"x", qos=1, packet_id=5) == bytes.fromhex("3206000174000578")


def table() -> IOTable:
    io = IOTable()
    io.add(Tag.new("A_DI", "DI"))
    io.add(Tag.new("B_AI", "AI", value=1.5))
    return io


def test_seq_counts_ndata_from_the_birth_and_wraps_at_255():
    io = table()
    node = sp.SparkplugPublisher(io, sp.SparkplugEndpoint(), "N", clock=lambda: 1.0)
    birth = sp.decode_payload(node.birth_payload())
    assert birth["seq"] == 0
    assert node.data_payload() is None  # nothing changed: nothing to send
    seqs = []
    for k in range(300):
        io.set("B_AI", float(k), Writer.SIM)
        seqs.append(sp.decode_payload(node.data_payload())["seq"])
    assert seqs[:3] == [1, 2, 3] and seqs[254:257] == [255, 0, 1]
    io.set("A_DI", True, Writer.SIM)
    data = sp.decode_payload(node.data_payload())
    assert data["metrics"] == [{"alias": 1, "datatype": sp.BOOLEAN, "value": True}]  # by alias


def test_the_birth_names_every_tag_with_alias_datatype_and_value():
    node = sp.SparkplugPublisher(table(), sp.SparkplugEndpoint(), "N", clock=lambda: 2.5)
    node.bdseq = 4
    birth = sp.decode_payload(node.birth_payload())
    assert birth["timestamp"] == 2500
    assert birth["metrics"] == [
        {"name": "bdSeq", "datatype": sp.INT64, "value": 4},
        {"name": "Node Control/Rebirth", "datatype": sp.BOOLEAN, "value": False},  # no alias
        {"name": "A_DI", "alias": 1, "datatype": sp.BOOLEAN, "value": False},
        {"name": "B_AI", "alias": 2, "datatype": sp.FLOAT, "value": 1.5},
    ]


@pytest.mark.parametrize("bad", ["", "a/b", "a+b", "a#"])
def test_ids_that_would_break_the_topic_fail(bad):
    with pytest.raises(ValueError, match="needs a name without"):
        sp.SparkplugPublisher(table(), sp.SparkplugEndpoint(), bad)
