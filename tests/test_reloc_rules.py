"""Rules of tools/build_reloc_220.py on small synthetic images (no firmware needed).

Covers the gaps reviews found: equal pointer words must still name the same string; a point's
plain words must stay equal (data points by value); a pointer to a suffix of a longer string is a
string pointer; text that looks like code pointers is still compared as text, and a code-pointer
table is accepted only through its targets (and listed).
"""
import importlib.util
import struct
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('build_reloc_220', Path(__file__).resolve().parents[1] / 'tools' / 'build_reloc_220.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def images():
    a = bytearray(m.BODY + 512)
    return a, bytearray(a)


class Rules(unittest.TestCase):
    def test_equal_pointer_word_with_changed_string_is_refused(self):
        a, b = images()
        struct.pack_into('<I', a, m.BODY, 0x53000100); struct.pack_into('<I', b, m.BODY, 0x53000100)
        a[m.BODY + 256:m.BODY + 261] = b'AAAA\0'; b[m.BODY + 256:m.BODY + 261] = b'BBBB\0'
        self.assertFalse(m.string_ok(0x53000100, 0x53000100, a, b))
        self.assertFalse(m.segment_ok([(a, b)], 0x53000000, 0x53000000, 0))
        b[m.BODY + 256:m.BODY + 261] = b'AAAA\0'
        self.assertTrue(m.segment_ok([(a, b)], 0x53000000, 0x53000000, 0))

    def test_point_plain_word_must_stay(self):
        a, b = images()
        a[m.BODY + 256:m.BODY + 261] = b'AAAA\0'; b[m.BODY + 256:m.BODY + 261] = b'AAAA\0'
        struct.pack_into('<II', a, m.BODY, 0x53000100, 1); struct.pack_into('<II', b, m.BODY, 0x53000100, 2)
        self.assertFalse(m.point_identity_ok([(a, b)], 0x53000000, 0x53000000))
        struct.pack_into('<I', b, m.BODY + 4, 1)
        self.assertTrue(m.point_identity_ok([(a, b)], 0x53000000, 0x53000000))

    def test_pointer_to_a_suffix_is_a_string_pointer(self):
        # C reads from the pointer to the NUL: a pointer into "prefixAAAA" naming "AAAA" is real.
        a, b = images()
        a[m.BODY + 256:m.BODY + 267] = b'prefixAAAA\0'; b[m.BODY + 256:m.BODY + 267] = b'prefixBBBB\0'
        struct.pack_into('<I', a, m.BODY, 0x53000106); struct.pack_into('<I', b, m.BODY, 0x53000106)
        self.assertEqual(m.cstring(bytes(a), 0x53000106, m.OLD), b'AAAA')
        self.assertFalse(m.string_ok(0x53000106, 0x53000106, a, b))
        self.assertFalse(m.segment_ok([(a, b)], 0x53000000, 0x53000000, 0))
        self.assertFalse(m.point_identity_ok([(a, b)], 0x53000000, 0x53000000))

    def test_data_point_compares_values_not_instruction_shape(self):
        at = m.DATA_START - m.RTOS
        a, b = bytearray(m.BODY + at + 64), bytearray(m.BODY + at + 64)
        struct.pack_into('<I', a, m.BODY + at, 0xEA000001); struct.pack_into('<I', b, m.BODY + at, 0xEA000002)
        self.assertFalse(m.point_identity_ok([(a, b)], m.DATA_START, m.DATA_START))

    def test_text_that_looks_like_code_pointers_is_still_compared_as_text(self):
        # "AAASBBBS" reads as two code-range words, but it is text: a changed text must be refused.
        a, b = images()
        a[m.BODY + 256:m.BODY + 265] = b'AAASBBBS\0'; b[m.BODY + 256:m.BODY + 265] = b'CCCSBBBS\0'
        struct.pack_into('<I', a, m.BODY, 0x53000100); struct.pack_into('<I', b, m.BODY, 0x53000100)
        self.assertEqual(m.cstring(bytes(a), 0x53000100, m.OLD), b'AAASBBBS')
        self.assertFalse(m.segment_ok([(a, b)], 0x53000000, 0x53000000, 0))

    def test_code_pointer_table_accepted_only_by_its_targets(self):
        # a vtable-like target (printable pointer bytes "@AAS DAAS") whose bytes differ passes only
        # when both entries' code targets have the same shape, and is then listed
        size = m.BODY + 0x414200
        a, b = bytearray(size), bytearray(size)
        struct.pack_into('<2I', a, m.BODY + 256, 0x53414140, 0x53414144); a[m.BODY + 264] = 0
        struct.pack_into('<2I', b, m.BODY + 256, 0x53414148, 0x5341414C); b[m.BODY + 264] = 0
        for img in (a, b):
            struct.pack_into('<24I', img, m.BODY + 0x414140, *([0xE12FFF1E] * 24))   # bx lr
        del m.AMBIGUOUS[:]
        self.assertTrue(m.string_ok(0x53000100, 0x53000100, a, b))
        self.assertEqual(m.AMBIGUOUS, [(0x53000100, 0x53000100)])
        struct.pack_into('<I', b, m.BODY + 0x414148, 0xE3A00000)                     # a target differs
        self.assertFalse(m.string_ok(0x53000100, 0x53000100, a, b))


if __name__ == '__main__':
    unittest.main()
