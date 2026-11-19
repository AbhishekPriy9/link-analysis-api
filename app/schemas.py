from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]
ServiceSource = Literal["website", "inferred"]
COMPETITOR_DISCLAIMER = (
    "Competitors are AI suggestions based on the page content and public "
    "knowledge. Verify them before using this analysis."
)


class BrandProfile(BaseModel):
    name: str = Field(description="Brand or company name shown on the page.")
    category: str = Field(description="Main business category in a few words.")
    summary: str = Field(
        description="Business overview, in two or three sentences."
    )
    products_services: list[str] = Field(
        description="Main products and services named on the page."
    )


class Competitor(BaseModel):
    name: str = Field(description="Name of a real competing company.")
    website: str = Field(
        description="Competitor website, or an empty string when unknown."
    )
    strengths: list[str] = Field(description="Where this competitor is strong.")
    weaknesses: list[str] = Field(description="Gaps or weaknesses of this competitor.")


class CompetitorAnalysis(BaseModel):
    brand_overview: str = Field(
        description="Summary of the submitted brand and its market."
    )
    main_category: str = Field(description="Main category the brand competes in.")
    products_services: list[str] = Field(
        description="Products and services the brand offers."
    )
    competitors: list[Competitor] = Field(
        description="Three or more real competitors where reliable information exists."
    )
    strengths: list[str] = Field(
        description="Advantages the brand has over these competitors."
    )
    opportunities: list[str] = Field(
        description="Practical ways to improve market positioning."
    )
    disclaimer: str = Field(
        min_length=1,
        description=(
            "Short note that competitors are AI suggestions and must be verified."
        ),
    )


class SearchQuery(BaseModel):
    query: str = Field(description="A search query a potential customer could use.")
    platforms: list[Platform] = Field(
        description=(
            "Platforms where the query fits: Google, Facebook, Instagram, "
            "LinkedIn or X."
        )
    )
    intent: str = Field(description="What the searcher is trying to do.")
    business_relevance: str = Field(
        description="Why the query matters to this business."
    )
    target_audience: str = Field(
        description="The audience most likely to use the query."
    )


class SeoImprovements(BaseModel):
    primary_keyword: str = Field(description="Single most important keyword.")
    secondary_keywords: list[str] = Field(
        description="Supporting keywords for the same offering."
    )
    long_tail_keywords: list[str] = Field(
        description="Longer, more specific keyword phrases."
    )
    business_keywords: list[str] = Field(
        description="Keywords tied to the business category."
    )
    recommended_title: str = Field(
        description="Suggested page title, under 60 characters."
    )
    meta_description: str = Field(
        description="Suggested meta description, under 160 characters."
    )
    supporting_content: list[str] = Field(
        description="Content pages or sections that would improve search visibility."
    )


class Service(BaseModel):
    name: str = Field(description="Name of the service.")
    description: str = Field(description="Short explanation of the service.")
    target_industry: str = Field(description="Industry that needs this service.")
    target_customer: str = Field(description="Type of customer that buys this service.")
    main_benefit: str = Field(description="Why the customer needs this service.")
    source: ServiceSource = Field(
        description='"website" when described on the page, otherwise "inferred".'
    )


class TargetAudience(BaseModel):
    primary_audience: list[str] = Field(description="Main audience groups.")
    secondary_audience: list[str] = Field(description="Supporting audience groups.")
    industries: list[str] = Field(description="Industry segments the business serves.")
    business_types: list[str] = Field(
        description="Business types that buy from this brand."
    )
    customer_profiles: list[str] = Field(
        description="Descriptions of typical customers."
    )
    location_focus: str = Field(
        description=(
            "Geographic focus stated on the page, or a note that none is stated."
        )
    )
    customer_needs: list[str] = Field(description="Problems and needs customers bring.")
    customer_interests: list[str] = Field(
        description="Topics these customers care about."
    )
    recommended_segments: list[str] = Field(
        description="Audience segments worth targeting in marketing."
    )


class Analysis(BaseModel):
    competitor_analysis: CompetitorAnalysis = Field(
        description="Competitor comparison for the submitted brand."
    )
    target_search_queries: list[SearchQuery] = Field(
        min_length=5,
        max_length=5,
        description="Five search queries customers may use.",
    )
    seo_improvements: SeoImprovements = Field(
        description="Keyword, title, meta description and content recommendations."
    )
    top_services: list[Service] = Field(
        min_length=5,
        max_length=5,
        description="The five most important services.",
    )
    target_audience: TargetAudience = Field(
        description="Primary and secondary audience groups."
    )


class LlmAnalysis(BaseModel):
    brand: BrandProfile = Field(description="Brand profile built from the page.")
    analysis: Analysis = Field(description="The five requested analysis sections.")


class LinkAnalysisRequest(BaseModel):
    url: str = Field(
        min_length=1,
        max_length=2048,
        description="Public http or https URL to analyse.",
    )


class AnalysisMeta(BaseModel):
    model: str = Field(description="Model that produced the analysis.")
    processing_ms: int = Field(
        description="Time spent on this request, in milliseconds."
    )
    fetched_at: datetime = Field(description="UTC time the page was fetched.")
    input_tokens: int | None = Field(
        description="Prompt tokens reported by the provider."
    )
    output_tokens: int | None = Field(
        description="Output tokens reported by the provider."
    )
    cached: bool = False


class LinkAnalysisResponse(BaseModel):
    success: Literal[True] = True
    website: str = Field(description="Normalised URL that was analysed.")
    brand: BrandProfile
    analysis: Analysis
    meta: AnalysisMeta


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error message.")


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
