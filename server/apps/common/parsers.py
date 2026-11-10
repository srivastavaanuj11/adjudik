"""YAML body parser for draft upload (SPEC §11.1, §11.8).

``application/yaml`` bodies are handed to the view as the raw text (a ``str``),
so the view can keep ``source_text`` verbatim and parse it with the core loader.
"""

from __future__ import annotations

from rest_framework.parsers import BaseParser


class YAMLParser(BaseParser):
    media_type = "application/yaml"

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read().decode("utf-8")
