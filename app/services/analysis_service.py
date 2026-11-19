import asyncio
from time import perf_counter
from weakref import WeakValueDictionary

from app.schemas import AnalysisMeta, LinkAnalysisResponse
from app.services.llm_service import LlmService
from app.services.website_extractor import FetchError, WebsiteExtractor
from app.utils.cache import TtlCache
from app.utils.validators import normalize_url


class LinkAnalysisService:
    def __init__(
        self,
        extractor: WebsiteExtractor,
        llm: LlmService,
        cache: TtlCache,
    ) -> None:
        self._extractor = extractor
        self._llm = llm
        self._cache = cache
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def analyze(self, url: str) -> LinkAnalysisResponse:
        normalized = normalize_url(url)

        response = self._cached(normalized)
        if response is not None:
            return response

        lock = self._locks.get(normalized)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[normalized] = lock

        async with lock:
            response = self._cached(normalized)
            if response is not None:
                return response

            started = perf_counter()
            content = await self._extractor.extract(normalized)
            if not content.text.strip():
                raise FetchError("The page did not contain readable text.")

            result = await self._llm.analyze(content)
            response = LinkAnalysisResponse(
                website=normalized,
                brand=result.analysis.brand,
                analysis=result.analysis.analysis,
                meta=AnalysisMeta(
                    model=result.model,
                    processing_ms=round((perf_counter() - started) * 1000),
                    fetched_at=content.fetched_at,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                ),
            )
            self._cache.set(normalized, response.model_dump(mode="json"))
            return response

    def _cached(self, url: str) -> LinkAnalysisResponse | None:
        cached = self._cache.get(url)
        if cached is None:
            return None

        response = LinkAnalysisResponse.model_validate(cached)
        response.meta.cached = True
        response.meta.processing_ms = 0
        return response
