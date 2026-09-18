"""mtgwiki: small, generic, data-oriented MediaWiki client."""

from .client import APIError, Client, DEFAULT_API_URL, DEFAULT_USER_AGENT
from .io import read_jsonl, write_json, write_jsonl
from .parser import clean_wikitext, parse_wikitext
from .wiki import DEFAULT_EXTRACT_INCLUDE, Wiki

__all__ = [
    "APIError",
    "Client",
    "DEFAULT_API_URL",
    "DEFAULT_USER_AGENT",
    "DEFAULT_EXTRACT_INCLUDE",
    "Wiki",
    "clean_wikitext",
    "parse_wikitext",
    "read_jsonl",
    "write_json",
    "write_jsonl",
]

__version__ = "1.2.0"
