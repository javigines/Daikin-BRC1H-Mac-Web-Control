import pytest

from madoka_protocol import Reassembler, split_in_chunks, parse_objects


@pytest.mark.parametrize("length", [4, 6, 18, 19, 20, 21, 37, 38, 39, 40, 57, 58, 76, 80, 254, 255])
def test_exact_length_and_no_premature_result(length):
    payload = bytearray([length, 0, 0, 0x40]) + bytearray(length - 4)
    chunks = split_in_chunks(payload)
    reasm = Reassembler()
    for chunk in chunks[:-1]:
        assert reasm.feed(chunk) is None
    assert reasm.feed(chunks[-1]) == payload
    assert reasm.chunks == []


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\x00\x00", b"\x00\x03\x00\x00",
                                      bytes(21), b"\x00\x06\x00", b"\x00\x04\x00\x00\x40\xff"])
def test_malformed_fragment_discards_incomplete_message(bad):
    r = Reassembler()
    chunks = split_in_chunks(bytearray([40]) + bytearray(39))
    assert r.feed(chunks[0]) is None
    assert r.feed(bad) is None
    assert r.chunks == []
    assert r.feed(chunks[1]) is None  # cannot continue an abandoned message
    assert r.feed(bytearray([0, 4, 0, 0, 0x40])) == bytearray([4, 0, 0, 0x40])


@pytest.mark.parametrize("indices", [[1, 2], [0, 2], [0, 1, 1, 2], [2, 1]])
def test_out_of_order_or_duplicate_continuation_is_discarded(indices):
    chunks = split_in_chunks(bytearray([40]) + bytearray(39))
    r = Reassembler()
    assert all(r.feed(chunks[i]) is None for i in indices)


def test_duplicate_start_restarts_without_counting_twice():
    payload = bytearray([40]) + bytearray(39)
    chunks = split_in_chunks(payload)
    r = Reassembler()
    assert r.feed(chunks[0]) is None
    assert r.feed(chunks[0]) is None
    assert r.feed(chunks[1]) is None
    assert r.feed(chunks[2]) == payload


def test_incomplete_message_can_be_reset_or_replaced_by_new_start():
    r = Reassembler()
    assert r.feed(split_in_chunks(bytearray([40]) + bytearray(39))[0]) is None
    r.reset()
    assert r.chunks == []
    assert r.feed(bytearray([0, 4, 0, 0, 0x40])) == bytearray([4, 0, 0, 0x40])


@pytest.mark.parametrize("payload", [b"", b"\x04\x00\x00", b"\x06\x00\x00\x40",
                                           b"\x05\x00\x00\x40\x20",
                                           b"\x07\x00\x00\x40\x20\x02\x01"])
def test_malformed_objects_are_not_returned_as_partial_success(payload):
    with pytest.raises(ValueError):
        parse_objects(payload)


def test_zero_size_objects_remain_supported():
    assert parse_objects(bytes([6, 0, 0, 0x40, 0x20, 0])) == {0x20: b""}
