import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DatabaseConfig:
    host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5432")))
    name: str = field(default_factory=lambda: os.getenv("POSTGRES_DB", "farmvaidya"))
    user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER", "farmvaidya"))
    password: str = field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD", ""))

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass
class VertexAIConfig:
    project: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    region: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_REGION", "asia-south1"))
    credentials_path: str = field(default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini-2.5-flash"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-multilingual-embedding-002"))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "512")))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))


@dataclass
class SonioxConfig:
    api_key: str = field(default_factory=lambda: os.getenv("SONIOX_API_KEY", ""))
    language: str = "te"  # Telugu ISO 639-1


@dataclass
class CartesiaConfig:
    api_key: str = field(default_factory=lambda: os.getenv("CARTESIA_API_KEY", ""))
    voice_id: str = field(default_factory=lambda: os.getenv("CARTESIA_TELUGU_VOICE_ID", ""))
    model_id: str = "sonic-3"
    language: str = ""  # empty = use voice's native language (Telugu voice auto-detected)


@dataclass
class TataTeleConfig:
    api_key: str = field(default_factory=lambda: os.getenv("TATA_TELE_API_KEY", ""))
    endpoint: str = field(default_factory=lambda: os.getenv("TATA_TELE_ENDPOINT", ""))
    did_number: str = field(default_factory=lambda: os.getenv("TATA_TELE_DID_NUMBER", ""))


@dataclass
class RAGConfig:
    top_k: int = field(default_factory=lambda: int(os.getenv("RAG_TOP_K", "5")))
    similarity_threshold: float = field(default_factory=lambda: float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.65")))
    embedding_dim: int = 768   # text-multilingual-embedding-002 output size
    chunk_size_tokens: int = 300
    chunk_overlap_tokens: int = 50


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: os.getenv("SERVER_HOST", "0.0.0.0"))
    http_port: int = field(default_factory=lambda: int(os.getenv("SERVER_PORT", "8080")))
    ws_port: int = field(default_factory=lambda: int(os.getenv("WEBSOCKET_PORT", "8765")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


@dataclass
class Settings:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    vertex: VertexAIConfig = field(default_factory=VertexAIConfig)
    soniox: SonioxConfig = field(default_factory=SonioxConfig)
    cartesia: CartesiaConfig = field(default_factory=CartesiaConfig)
    tata_tele: TataTeleConfig = field(default_factory=TataTeleConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


settings = Settings()
