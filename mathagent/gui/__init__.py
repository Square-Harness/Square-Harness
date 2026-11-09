# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Visual interface: a local web app served by the harness (square-harness --gui)."""
from .server import serve

__all__ = ['serve']
