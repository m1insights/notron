"""Managed content storage; never infer an old repository cache as a fallback."""
from pathlib import Path

DATA_DIR = Path.home() / 'Library' / 'Application Support' / 'com.m1labs.notron'
