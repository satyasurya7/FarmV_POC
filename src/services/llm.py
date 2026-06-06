"""Vertex AI LLM service factory for pipecat 1.3.x."""

from pipecat.services.google.vertex.llm import GoogleVertexLLMService, GoogleVertexLLMSettings

from src.config import settings


def create_llm_service() -> GoogleVertexLLMService:
    return GoogleVertexLLMService(
        credentials_path=settings.vertex.credentials_path,
        project_id=settings.vertex.project,
        location=settings.vertex.region,
        settings=GoogleVertexLLMSettings(
            model=settings.vertex.llm_model,
            temperature=settings.vertex.llm_temperature,
            max_tokens=settings.vertex.llm_max_tokens,
        ),
    )
