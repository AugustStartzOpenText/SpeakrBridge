"""Template-driven scoping document generation."""

from scoping.catalog import ScopingTemplateCatalog
from scoping.extraction import ScopingExtractionResult, ScopingExtractor, extraction_to_word_values
from scoping.models import ScopingTemplate
from scoping.word_writer import WordScopingWriter

__all__ = [
    "ScopingExtractionResult",
    "ScopingExtractor",
    "ScopingTemplate",
    "ScopingTemplateCatalog",
    "WordScopingWriter",
    "extraction_to_word_values",
]
