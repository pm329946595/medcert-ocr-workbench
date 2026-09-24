"""Small deterministic format checks; not authenticity verification."""
ALPHABET = '0123456789ABCDEFGHJKLMNPQRTUWXY'
WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def credit_valid(text):
    """GB 32100 character, region digit and checksum constraints."""
    return (len(text)==18 and all(c in ALPHABET for c in text) and text[2:8].isdigit()
            and ALPHABET[(-sum(ALPHABET.index(c)*w for c,w in zip(text[:17],WEIGHTS)))%31]==text[-1])
