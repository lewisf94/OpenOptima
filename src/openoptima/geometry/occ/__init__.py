"""Built-in OpenCASCADE geometry provider."""

from .provider import OccGeometryProvider
from .templates import Template, available_templates, get_template, register

__all__ = [
    "OccGeometryProvider",
    "Template",
    "available_templates",
    "get_template",
    "register",
]
