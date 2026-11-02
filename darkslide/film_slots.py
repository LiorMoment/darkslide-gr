"""Menu slot identities for the independently replaceable film positions.

Menu order and mask order are deliberately different: the existing four bits
remain stable, and Vivid uses the fifth bit. Positive and Negative are excluded.
"""

LEGACY_SLOTS = (8, 9, 10, 11)
SUPPORTED_SLOTS = (2, 8, 9, 10, 11)
SUPPORTED_KEYS = tuple(str(slot) for slot in SUPPORTED_SLOTS)
SLOT_BITS = {8: 0, 9: 1, 10: 2, 11: 3, 2: 4}
SLOT_TONES = {2: 12, 8: 18, 9: 19, 10: 20, 11: 21}


def mask_slots(slots):
    """Return a stable five-bit mask; reject ambiguous or repeated identities."""
    if (type(slots) not in (list, tuple) or
            any(type(slot) is not int or slot not in SLOT_BITS for slot in slots) or
            len(slots) != len(set(slots))):
        raise ValueError("invalid film slots")
    return sum(1 << SLOT_BITS[slot] for slot in slots)
