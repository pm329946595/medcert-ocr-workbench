"""Document parsers share OCR entries; general OCR does not invoke this registry."""
from functools import partial
from .schema import PROFILES
from .common import parse_document

PARSERS = {key:partial(parse_document,document_type=key) for key in PROFILES}
