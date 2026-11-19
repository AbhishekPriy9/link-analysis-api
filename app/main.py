import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.schemas import (
    ErrorDetail,
    ErrorResponse,
    LinkAnalysisRequest,
    LinkAnalysisResponse,
)
from app.services.analysis_service import LinkAnalysisService
from app.services.llm_service import LlmError, LlmNotConfiguredError, LlmService
from app.services.website_extractor import FetchError, WebsiteExtractor
from app.utils.cache import TtlCache
from app.utils.validators import InvalidUrlError

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(mode="json")
    )


def _build_service(settings: Settings) -> LinkAnalysisService:
    extractor = WebsiteExtractor(
        fetch_timeout_seconds=settings.fetch_timeout_seconds,
        max_response_bytes=settings.max_response_bytes,
        max_content_chars=settings.max_content_chars,
        max_redirects=settings.max_redirects,
        user_agent=settings.user_agent,
    )
    llm = LlmService(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return LinkAnalysisService(
        extractor=extractor,
        llm=llm,
        cache=TtlCache(settings.cache_ttl_seconds),
    )


def create_app(
    service: LinkAnalysisService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.analysis_service is None:
            application.state.analysis_service = _build_service(app_settings)
        yield

    app = FastAPI(title="Link Analysis API", version="1.0.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.analysis_service = service

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        message = "The request body is invalid."
        if errors:
            location = ".".join(str(part) for part in errors[0].get("loc", ()))
            detail = errors[0].get("msg", "invalid value")
            message = f"{location}: {detail}" if location else str(detail)
        return _error_response(422, "invalid_request", message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error during request")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    @app.get("/health")
    async def health(request: Request) -> dict[str, str | bool]:
        current: Settings = request.app.state.settings
        return {
            "status": "ok",
            "model": current.gemini_model,
            "llm_configured": current.gemini_api_key is not None,
        }

    @app.post(
        "/link-analysis",
        response_model=LinkAnalysisResponse,
        summary=(
            "Analyse a website and return business, competitor, SEO, service "
            "and audience insights"
        ),
    )
    async def link_analysis(
        payload: LinkAnalysisRequest, request: Request
    ) -> LinkAnalysisResponse:
        analysis_service: LinkAnalysisService | None = (
            request.app.state.analysis_service
        )
        if analysis_service is None:
            raise AppError(503, "not_ready", "The analysis service is not ready.")

        try:
            return await analysis_service.analyze(payload.url)
        except AppError:
            raise
        except InvalidUrlError as exc:
            raise AppError(400, "invalid_url", str(exc)) from exc
        except LlmNotConfiguredError as exc:
            raise AppError(
                503, "llm_not_configured", "The language model is not configured."
            ) from exc
        except FetchError as exc:
            raise AppError(502, "fetch_failed", str(exc)) from exc
        except LlmError as exc:
            raise AppError(502, "llm_failed", str(exc)) from exc
        except Exception as exc:
            logger.exception("Unhandled error during link analysis")
            raise AppError(
                500, "internal_error", "An unexpected error occurred."
            ) from exc

    return app


app = create_app()
