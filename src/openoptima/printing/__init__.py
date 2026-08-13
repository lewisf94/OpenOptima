"""Measuring a shape against a printer.

The rules and settings live in ``domain/printing.py``; this package holds the
mesh geometry, which needs trimesh and therefore may not live in the domain.
"""

from .overhang import PrintabilityReport, measure_printability

__all__ = ["PrintabilityReport", "measure_printability"]
