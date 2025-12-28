# -*- coding: utf-8 -*-
from __future__ import annotations
import logging
import sys

def setup_logging():
    logger = logging.getLogger("vocab_service")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)
    return logger
