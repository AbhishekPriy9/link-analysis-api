import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from google.genai import _interactions_errors

from app.schemas import COMPETITOR_DISCLAIMER, LlmAnalysis
from app.services import llm_service
from app.services.llm_service import LlmError, LlmNotConfiguredError, LlmService
from app.services.website_extractor import WebsiteContent


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
                    "platforms": ["Google", "LinkedIn"],
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
        headings=["Acme Web Studio", "Services"],
        text="We build fast marketing websites for small teams.",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class FakeApiError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _interaction_error(cls, status_code: int):
    response = httpx.Response(
        status_code, request=httpx.Request("POST", "https://example.com")
    )
    return cls("provider detail", response=response, body={"error": "detail"})


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens = input_tokens
        self.total_output_tokens = output_tokens


class FakeInteraction:
    def __init__(
        self,
        output_text: str,
        status: str = "completed",
        usage: FakeUsage | None = None,
    ) -> None:
        self.output_text = output_text
        self.status = status
        self.usage = usage


class FakeInteractions:
    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _service(
    monkeypatch,
    interactions: FakeInteractions,
    *,
    api_key: str | None = "test-key",
    model: str = "gemini-3.8-flash",
    fallback_model: str = "gemini-3.5-flash-lite",
) -> LlmService:
    monkeypatch.setattr(
        llm_service.genai,
        "Client",
        lambda api_key: SimpleNamespace(
            aio=SimpleNamespace(interactions=interactions)
        ),
    )
    return LlmService(
        api_key=api_key,
        model=model,
        fallback_model=fallback_model,
        timeout_seconds=30.0,
    )


async def test_analyze_parses_the_structured_response(monkeypatch) -> None:
    analysis = LlmAnalysis.model_validate(_payload())
    interactions = FakeInteractions(
        [FakeInteraction(analysis.model_dump_json(), usage=FakeUsage(1200, 800))]
    )
    service = _service(monkeypatch, interactions)

    result = await service.analyze(_content())

    assert result.model == "gemini-3.8-flash"
    assert result.analysis.brand.name == "Acme Web Studio"
    assert result.analysis.analysis.competitor_analysis.disclaimer == COMPETITOR_DISCLAIMER
    assert result.input_tokens == 1200
    assert result.output_tokens == 800

    assert len(interactions.calls) == 1
    call = interactions.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert call["timeout"] == 30.0
    assert call["generation_config"] == {
        "max_output_tokens": 16384,
        "thinking_level": "medium",
    }
    assert call["store"] is False
    assert call["automatic_continuation"] is False
    assert call["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": LlmAnalysis.model_json_schema(),
    }
    assert "<page_content>" in call["input"]
    assert "We build fast marketing websites" in call["input"]
    assert "search volume" in call["system_instruction"]
    assert "exactly five search queries" in call["system_instruction"]


async def test_analyze_retries_the_fallback_model_on_rate_limit(monkeypatch) -> None:
    analysis = LlmAnalysis.model_validate(_payload())
    interactions = FakeInteractions(
        [
            _interaction_error(_interactions_errors.RateLimitError, 429),
            FakeInteraction(analysis.model_dump_json(), usage=FakeUsage(10, 20)),
        ]
    )
    service = _service(monkeypatch, interactions)

    result = await service.analyze(_content())

    assert [call["model"] for call in interactions.calls] == [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]
    assert result.model == "gemini-3.5-flash-lite"


async def test_analyze_does_not_retry_non_retryable_errors(monkeypatch) -> None:
    interactions = FakeInteractions([FakeApiError(400)])
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError, match="HTTP 400"):
        await service.analyze(_content())

    assert len(interactions.calls) == 1


async def test_analyze_hides_provider_details(monkeypatch) -> None:
    interactions = FakeInteractions(
        [
            _interaction_error(_interactions_errors.InternalServerError, 503),
            _interaction_error(_interactions_errors.InternalServerError, 503),
        ]
    )
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError) as exc_info:
        await service.analyze(_content())

    assert "internal cluster detail" not in str(exc_info.value)
    assert len(interactions.calls) == 2


async def test_analyze_rejects_malformed_json(monkeypatch) -> None:
    interactions = FakeInteractions([FakeInteraction('{"brand": {}}')])
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError, match="did not match"):
        await service.analyze(_content())


async def test_analyze_handles_missing_usage(monkeypatch) -> None:
    analysis = LlmAnalysis.model_validate(_payload())
    interactions = FakeInteractions([FakeInteraction(analysis.model_dump_json())])
    service = _service(monkeypatch, interactions)

    result = await service.analyze(_content())

    assert result.input_tokens is None
    assert result.output_tokens is None


async def test_analyze_rejects_the_wrong_number_of_queries(monkeypatch) -> None:
    analysis = LlmAnalysis.model_validate(_payload())
    payload = analysis.model_dump(mode="json")
    payload["analysis"]["target_search_queries"] = payload["analysis"][
        "target_search_queries"
    ][:4]
    interactions = FakeInteractions([FakeInteraction(json.dumps(payload))])
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError, match="did not match"):
        await service.analyze(_content())


async def test_analyze_rejects_incomplete_status(monkeypatch) -> None:
    interactions = FakeInteractions(
        [FakeInteraction('{"brand": {}}', status="incomplete")]
    )
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError, match="did not complete"):
        await service.analyze(_content())


async def test_analyze_rejects_empty_output(monkeypatch) -> None:
    interactions = FakeInteractions([FakeInteraction("  ")])
    service = _service(monkeypatch, interactions)

    with pytest.raises(LlmError, match="empty"):
        await service.analyze(_content())


async def test_analyze_requires_an_api_key(monkeypatch) -> None:
    def fail_if_called(api_key: str):
        raise AssertionError("the client must not be created without a key")

    monkeypatch.setattr(llm_service.genai, "Client", fail_if_called)
    service = LlmService(
        api_key=None,
        model="gemini-3.8-flash",
        fallback_model="gemini-3.5-flash-lite",
        timeout_seconds=30.0,
    )

    with pytest.raises(LlmNotConfiguredError):
        await service.analyze(_content())
