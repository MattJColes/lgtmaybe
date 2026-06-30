"""lgtmaybe.engine — the review pipeline (Track C)."""

from .astgrep import SymbolResolver, ast_grep_available, build_symbol_resolver
from .compress import batch_files, context_lines_for_budget, count_tokens
from .engine import LLMReviewEngine, ReviewIncompleteError
from .injection import wrap_diff
from .parse import ParseError, parse_findings
from .prompt import build_system_prompt
from .redact import redact
from .reflect import reflect_findings
from .retrieve import FileFetcher, resolve_needs

__all__ = [
    "LLMReviewEngine",
    "ReviewIncompleteError",
    # building blocks
    "batch_files",
    "context_lines_for_budget",
    "count_tokens",
    "wrap_diff",
    "ParseError",
    "parse_findings",
    "build_system_prompt",
    "redact",
    "reflect_findings",
    "FileFetcher",
    "resolve_needs",
    "SymbolResolver",
    "ast_grep_available",
    "build_symbol_resolver",
]
