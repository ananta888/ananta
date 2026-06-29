"""LangChain BaseRetriever Adapter für CodeCompassRetriever (LCG-041).

Implementiert BaseRetriever aus langchain-core, delegiert an CodeCompassRetriever.
Nur importierbar wenn langchain-core installiert ist.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

_IMPORT_ERROR_MSG = (
    "langchain-core is required for LangChainCodeCompassRetriever. "
    "Install it with: pip install 'ananta[langchain]'"
)


def _require_langchain_core() -> Any:
    try:
        from langchain_core.retrievers import BaseRetriever  # type: ignore
        return BaseRetriever
    except ImportError as exc:
        raise ImportError(_IMPORT_ERROR_MSG) from exc


class LangChainCodeCompassRetriever:
    """Thin wrapper that adapts CodeCompassRetriever to LangChain's BaseRetriever protocol.

    Lazy-inherits from BaseRetriever so this module can be imported
    without langchain-core installed (it raises ImportError only at
    instantiation time).
    """

    _base_class: Any = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "LangChainCodeCompassRetriever":
        BaseRetriever = _require_langchain_core()
        if cls._base_class is None:
            # Dynamically create the actual class with proper inheritance
            cls._base_class = type(
                "LangChainCodeCompassRetrieverImpl",
                (BaseRetriever,),
                {
                    "__init__": cls._impl_init,
                    "_get_relevant_documents": cls._impl_get_relevant_documents,
                    "_aget_relevant_documents": cls._impl_aget_relevant_documents,
                }
            )
        instance = cls._base_class.__new__(cls._base_class)
        instance.__init__(*args, **kwargs)
        return instance  # type: ignore

    @staticmethod
    def _impl_init(self: Any, scope: str = "codecompass_vector",
                   max_results: int = 5, **kwargs: Any) -> None:
        from worker.retrieval.codecompass_retriever import CodeCompassRetriever
        self._cc_retriever = CodeCompassRetriever(scope=scope)
        self._max_results = max_results

    @staticmethod
    def _impl_get_relevant_documents(self: Any, query: str, **kwargs: Any) -> list:
        try:
            from langchain_core.documents import Document  # type: ignore
        except ImportError:
            return []
        k = kwargs.get("k", self._max_results)
        result = self._cc_retriever.query(query, max_results=k)
        sources = result.get("sources", [])
        return [
            Document(
                page_content=s.get("content", ""),
                metadata={"path": s.get("path", ""), "score": s.get("score", 0.0)},
            )
            for s in sources
        ]

    @staticmethod
    async def _impl_aget_relevant_documents(self: Any, query: str, **kwargs: Any) -> list:
        import asyncio
        return await asyncio.to_thread(
            LangChainCodeCompassRetriever._impl_get_relevant_documents,
            self, query, **kwargs
        )
