import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import AnalysisMeta, LinkAnalysisResponse, LlmAnalysis
from app.services.analysis_service import LinkAnalysisService
from app.services.llm_service import LlmError, LlmResult, LlmService
from app.services.website_extractor import FetchError, WebsiteContent, WebsiteExtractor
from app.utils.cache import TtlCache

API_KEY_SETTINGS = Settings(gemini_api_key="test-key")
NO_KEY_SETTINGS = Settings(gemini_api_key=None)


def _payload() -> dict:
    return {
        "brand": {
            "name": "Acme Web Studio",
            "category": "Web design agency",
            "summary": "Acme designs and builds marketing websites for small teams.",
            "products_services": ["Web design", "Web development"],
        },
        "analysis": {
            "competitor_analysis": {
                "brand_overview": (
                    "Acme serves small teams that need a clear web presence."
                ),
                "main_category": "Web design",
                "products_services": ["Web design"],
                "competitors": [
                    {
                        "name": "Example Competitor",
                        "website": "https://competitor.example",
                        "strengths": ["Established brand"],
                        "weaknesses": ["Slow delivery"],
                    }
                ],
                "strengths": ["Fast delivery"],
                "opportunities": ["Publish case studies"],
                "disclaimer": "Competitors are AI suggestions and must be verified.",
            },
            "target_search_queries": [
                {
                    "query": f"web design for small teams {index}",
                    "platforms": ["Google"],
                    "intent": "service search",
                    "business_relevance": "High",
                    "target_audience": "small teams",
                }
                for index in range(5)
            ],
            "seo_improvements": {
                "primary_keyword": "web design for small teams",
                "secondary_keywords": ["marketing website"],
                "long_tail_keywords": ["web design for small saas teams"],
                "business_keywords": ["web design agency"],
                "recommended_title": "Web Design for Small Teams | Acme",
                "meta_description": (
                    "Acme builds fast marketing websites for small teams."
                ),
                "supporting_content": ["Service pages", "Case studies"],
            },
            "top_services": [
                {
                    "name": f"Service {index}",
                    "description": "A service.",
                    "target_industry": "SaaS",
                    "target_customer": "Small teams",
                    "main_benefit": "A clear online presence",
                    "source": "website",
                }
                for index in range(5)
            ],
            "target_audience": {
                "primary_audience": ["Small teams"],
                "secondary_audience": ["Startups"],
                "industries": ["SaaS"],
                "business_types": ["Agencies"],
                "customer_profiles": ["Founders"],
                "location_focus": "No location stated on the page.",
                "customer_needs": ["A credible website"],
                "customer_interests": ["Conversion"],
                "recommended_segments": ["Startups"],
            },
        },
    }


def _content() -> WebsiteContent:
    return WebsiteContent(
        url="https://example.com/",
        final_url="https://example.com/",
        title="Acme Web Studio",
        meta_description="Web design for small teams.",
        headings=["Acme Web Studio"],
        text="We build fast marketing websites for small teams.",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _llm_result() -> LlmResult:
    return LlmResult(
        analysis=LlmAnalysis.model_validate(_payload()),
        model="gemini-3.8-flash",
        input_tokens=1200,
        output_tokens=800,
    )


def _response() -> LinkAnalysisResponse:
    result = _llm_result()
    return LinkAnalysisResponse(
        website="https://example.com/",
        brand=result.analysis.brand,
        analysis=result.analysis.analysis,
        meta=AnalysisMeta(
            model=result.model,
            processing_ms=4321,
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        ),
    )


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class FixedService:
    def __init__(
        self,
        response: LinkAnalysisResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.urls: list[str] = []

    async def analyze(self, url: str) -> LinkAnalysisResponse:
        self.urls.append(url)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class StubExtractor:
    def __init__(self, content: WebsiteContent) -> None:
        self._content = content
        self.calls = 0

    async def extract(self, url: str) -> WebsiteContent:
        self.calls += 1
        return self._content


class SlowExtractor(StubExtractor):
    async def extract(self, url: str) -> WebsiteContent:
        await asyncio.sleep(0.05)
        return await super().extract(url)


class FailingExtractor:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def extract(self, url: str) -> WebsiteContent:
        raise self._error


class StubLlm:
    def __init__(self, result: LlmResult) -> None:
        self._result = result
        self.calls = 0

    async def analyze(self, content: WebsiteContent) -> LlmResult:
        self.calls += 1
        return self._result


def _real_service(
    *,
    extractor: object,
    llm: object,
) -> LinkAnalysisService:
    return LinkAnalysisService(
        extractor=extractor,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        cache=TtlCache(60),
    )


async def test_link_analysis_returns_the_five_sections() -> None:
    service = FixedService(response=_response())
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["website"] == "https://example.com/"
    assert body["brand"]["name"] == "Acme Web Studio"
    assert len(body["analysis"]["target_search_queries"]) == 5
    assert len(body["analysis"]["top_services"]) == 5
    assert body["analysis"]["competitor_analysis"]["disclaimer"]
    assert body["meta"]["cached"] is False
    assert service.urls == ["https://example.com/"]


async def test_private_url_returns_400_without_fetching() -> None:
    fetched: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request)
        return httpx.Response(200, text="should not be fetched")

    extractor = WebsiteExtractor(
        fetch_timeout_seconds=5.0,
        max_response_bytes=1000,
        max_content_chars=1000,
        max_redirects=2,
        user_agent="TestBot/1.0",
        transport=httpx.MockTransport(handler),
    )
    service = LinkAnalysisService(
        extractor=extractor,
        llm=StubLlm(_llm_result()),
        cache=TtlCache(60),
    )
    app = create_app(service=service, settings=NO_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "http://127.0.0.1/"}
        )

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": {
            "code": "invalid_url",
            "message": "The URL resolves to a private or internal address.",
        },
    }
    assert fetched == []


async def test_invalid_scheme_returns_400_before_fetching() -> None:
    extractor = StubExtractor(_content())
    service = _real_service(
        extractor=extractor,
        llm=StubLlm(_llm_result()),
    )
    app = create_app(service=service, settings=NO_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "ftp://example.com/file"}
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"
    assert extractor.calls == 0


async def test_missing_url_returns_422() -> None:
    app = create_app(
        service=FixedService(response=_response()), settings=API_KEY_SETTINGS
    )

    async with _client(app) as client:
        response = await client.post("/link-analysis", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "invalid_request"
    assert "url" in body["error"]["message"]


async def test_missing_api_key_returns_503() -> None:
    llm = LlmService(
        api_key=None,
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        timeout_seconds=5.0,
    )
    service = _real_service(extractor=StubExtractor(_content()), llm=llm)
    app = create_app(service=service, settings=NO_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


async def test_fetch_failure_returns_502() -> None:
    service = _real_service(
        extractor=FailingExtractor(FetchError("The website returned HTTP 404.")),
        llm=StubLlm(_llm_result()),
    )
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/missing"}
        )

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "fetch_failed",
        "message": "The website returned HTTP 404.",
    }


async def test_empty_page_is_not_sent_to_the_model() -> None:
    empty = WebsiteContent(
        url="https://example.com/",
        final_url="https://example.com/",
        title="",
        meta_description="",
        headings=[],
        text="",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    llm = StubLlm(_llm_result())
    service = _real_service(extractor=StubExtractor(empty), llm=llm)
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "fetch_failed"
    assert llm.calls == 0


async def test_llm_failure_returns_502() -> None:
    service = FixedService(error=LlmError("The language model request failed."))
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_failed"


async def test_unexpected_error_is_hidden() -> None:
    service = FixedService(error=RuntimeError("database password leaked"))
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        response = await client.post(
            "/link-analysis", json={"url": "https://example.com/"}
        )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "An unexpected error occurred.",
    }
    assert "database password leaked" not in response.text


async def test_second_request_is_served_from_cache() -> None:
    extractor = StubExtractor(_content())
    llm = StubLlm(_llm_result())
    service = _real_service(extractor=extractor, llm=llm)
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        first = await client.post(
            "/link-analysis", json={"url": "https://example.com/path#top"}
        )
        second = await client.post(
            "/link-analysis", json={"url": "https://example.com/path"}
        )

    assert first.status_code == 200
    assert first.json()["website"] == "https://example.com/path"
    assert first.json()["meta"]["cached"] is False
    assert second.status_code == 200
    assert second.json()["meta"]["cached"] is True
    assert second.json()["meta"]["processing_ms"] == 0
    assert extractor.calls == 1
    assert llm.calls == 1


async def test_concurrent_requests_share_one_analysis() -> None:
    extractor = SlowExtractor(_content())
    llm = StubLlm(_llm_result())
    service = _real_service(extractor=extractor, llm=llm)
    app = create_app(service=service, settings=API_KEY_SETTINGS)

    async with _client(app) as client:
        first, second = await asyncio.gather(
            client.post("/link-analysis", json={"url": "https://example.com/"}),
            client.post("/link-analysis", json={"url": "https://example.com/"}),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert extractor.calls == 1
    assert llm.calls == 1
    assert first.json()["meta"]["cached"] is False
    assert second.json()["meta"]["cached"] is True


@pytest.mark.parametrize(
    ("api_key", "expected"),
    [("test-key", True), (None, False)],
)
async def test_health_reports_configuration(
    api_key: str | None, expected: bool
) -> None:
    app = create_app(
        service=FixedService(response=_response()),
        settings=Settings(gemini_api_key=api_key),
    )

    async with _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": "gemini-3.5-flash-lite",
        "llm_configured": expected,
    }


def test_lifespan_builds_the_service() -> None:
    app = create_app(settings=API_KEY_SETTINGS)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert isinstance(app.state.analysis_service, LinkAnalysisService)
