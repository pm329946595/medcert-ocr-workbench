"""Backward-compatible entry point using the shared field parser.

The previous implementation is frozen in evaluation/runs/baseline_20260916/code.
"""
from .schema import PROFILES
from .validators import credit_valid
from .common import compact, parse_document

FIELDS = [(f.key,f.label,f.aliases) for f in PROFILES["business_license"].fields]


def parse_business_license(entries,image_size,image=None,reread=None):
    return parse_document(entries,image_size,image=image,reread=reread,document_type="business_license")
