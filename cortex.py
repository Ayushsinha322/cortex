#!/usr/bin/env python3
"""Standalone entry point: python3 cortex.py [root] [options]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cortex.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
