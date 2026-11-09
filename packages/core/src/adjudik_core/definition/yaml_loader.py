"""A hardened YAML loader for decision definitions (SPEC §5.6).

Definition YAML is loaded with a ``SafeLoader`` subclass that rejects anchors,
aliases and merge keys, and documents larger than 1 MB. This blocks billion-laughs
style expansion and merge-key surprises in author-supplied definitions.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.constructor import ConstructorError

MAX_DOCUMENT_BYTES = 1_000_000  # 1 MB (SPEC §5.6)

_MERGE_TAG = "tag:yaml.org,2002:merge"


class YAMLLoadError(ValueError):
    """The YAML document is too large or uses a forbidden feature."""


class AdjudikSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader that forbids anchors, aliases and merge keys."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            event = self.get_event()
            raise ConstructorError(
                None,
                None,
                "YAML aliases (*ref) are not allowed in definitions",
                event.start_mark,
            )
        event = self.peek_event()
        if event.anchor is not None:
            raise ConstructorError(
                None,
                None,
                "YAML anchors (&name) are not allowed in definitions",
                event.start_mark,
            )
        return super().compose_node(parent, index)

    def flatten_mapping(self, node: Any) -> Any:
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:
                raise ConstructorError(
                    None,
                    None,
                    "YAML merge keys (<<) are not allowed in definitions",
                    key_node.start_mark,
                )
        return super().flatten_mapping(node)


def load_yaml(text: str | bytes) -> Any:
    """Parse one YAML document with the hardened loader (SPEC §5.6).

    Raises :class:`YAMLLoadError` for oversize documents or forbidden features,
    and for multi-document streams.
    """
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise YAMLLoadError(f"definition document exceeds {MAX_DOCUMENT_BYTES} bytes (SPEC §5.6)")
    try:
        # load_all so we can reject multi-document streams explicitly.
        docs = list(yaml.load_all(raw, Loader=AdjudikSafeLoader))
    except (ConstructorError, yaml.YAMLError) as exc:
        raise YAMLLoadError(str(exc)) from exc
    if len(docs) == 0:
        return None
    if len(docs) > 1:
        raise YAMLLoadError("a definition must be a single YAML document")
    return docs[0]
