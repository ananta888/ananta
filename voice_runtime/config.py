from __future__ import annotations

import os
from dataclasses import dataclass

PIPELINES = frozenset(
    {
        "simple",
        "oldschool_light",
        "whisper_cpp",
        "realtime_streaming",
        "meeting",
        "confidence_rerun",
        "custom",
    }
)
ASR_BACKENDS = frozenset({"mock", "voxtral", "vosk", "whisper_cpp", "faster_whisper"})
VAD_BACKENDS = frozenset({"mock", "none", "passthrough", "webrtcvad", "silero"})
POSTPROCESS_BACKENDS = frozenset({"none", "off", "disabled", "rules", "rule_based", "glossary", "llm", "llm_corrector"})
DIARIZATION_BACKENDS = frozenset({"none", "off", "disabled", "mock", "pyannote"})
TRANSPORT_MODES = frozenset({"batch", "stream"})
RECOGNITION_STRATEGIES = frozenset({"single", "classic_then_correct", "parallel_compare", "parallel_fusion"})
ROUTING_STRATEGIES = frozenset({"fixed", "adaptive_local"})
CORRECTION_POLICIES = frozenset({"none", "rules", "restricted_choice", "local_schema_corrector"})
REVIEW_POLICIES = frozenset({"automatic", "on_disagreement", "always"})
ENHANCEMENT_VARIANTS = frozenset({"original", "bypass", "normalized", "high_pass", "speech_safe"})
FASTER_WHISPER_COMPUTE_TYPES = frozenset(
    {"default", "auto", "int8", "int8_float16", "int8_float32", "int16", "float16", "float32", "bfloat16"}
)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _choice(value: str | None, *, default: str, allowed: frozenset[str], name: str) -> str:
    normalized = str(value or default).strip().lower() or default
    if normalized not in allowed:
        raise ValueError(f"unsupported {name}: {normalized}")
    return normalized


def _csv_tuple(value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    parsed = tuple(item.strip().lower() for item in str(value or "").split(",") if item.strip())
    return parsed or default


@dataclass(frozen=True)
class VoiceRuntimeConfig:
    host: str = "0.0.0.0"
    port: int = 8090
    provider: str = "voice-runtime"
    backend: str = "mock"
    model: str = "voxtral"
    fallback_model: str = "whisper-small"
    timeout_sec: int = 120
    max_audio_mb: int = 25
    enable_streaming: bool = False
    store_audio: bool = False
    device: str = "auto"
    model_path: str | None = None
    voxtral_runner_path: str | None = None
    voxtral_runner_style: str = "realtime"
    backend_fallback_order: tuple[str, ...] = ("voxtral", "mock")
    transcription_pipeline: str = "simple"
    vad_backend: str = "mock"
    silero_vad_model_path: str | None = None
    silero_vad_threshold: float = 0.5
    asr_backend: str = "mock"
    postprocess_backend: str = "none"
    confidence_rerun_enabled: bool = False
    confidence_threshold: float = 0.7
    rerun_backend: str = "mock"
    rerun_max_segments: int = 3
    rerun_max_audio_ms: int = 30_000
    diarization_backend: str = "none"
    glossary_path: str | None = None
    vosk_model_path: str | None = None
    whisper_cpp_bin: str | None = None
    whisper_cpp_model_path: str | None = None
    whisper_cpp_extra_args: tuple[str, ...] = ()
    whisper_cpp_threads: int = 4
    whisper_cpp_gpu_layers: int = 0
    whisper_cpp_beam_size: int = 5
    whisper_cpp_temperature: float = 0.0
    whisper_cpp_prompt_max_chars: int = 512
    faster_whisper_model_path: str | None = None
    faster_whisper_compute_type: str = "default"
    faster_whisper_beam_size: int = 5
    faster_whisper_vad_filter: bool = False
    faster_whisper_vad_min_silence_ms: int = 500
    transport_mode: str = "batch"
    recognition_strategy: str = "single"
    routing_strategy: str = "fixed"
    correction_policy: str = "none"
    review_policy: str = "automatic"
    primary_backend: str = "mock"
    secondary_backends: tuple[str, ...] = ()
    max_parallel_backends: int = 2
    candidate_deadline_sec: float = 120.0
    max_audio_duration_sec: int = 3600
    max_decoded_pcm_mb: int = 256
    max_queue_depth: int = 16
    resource_max_ram_mb: int = 16_384
    resource_max_vram_mb: int = 24_576
    resource_max_concurrent_backends: int = 8
    resource_max_audio_seconds: int = 3_600
    resource_max_queue_depth: int = 16
    production_profile: bool = False
    allow_model_download: bool = False
    model_manifest_path: str | None = None
    model_root: str | None = None
    calibration_path: str | None = None
    internal_service_token: str | None = None
    policy_allowed_backends: tuple[str, ...] = ("vosk", "whisper_cpp", "faster_whisper", "voxtral")
    policy_allowed_recognition_strategies: tuple[str, ...] = (
        "single",
        "parallel_compare",
        "classic_then_correct",
    )
    policy_allowed_routing_strategies: tuple[str, ...] = ("fixed", "adaptive_local")
    policy_allowed_correction_policies: tuple[str, ...] = (
        "none",
        "rules",
        "restricted_choice",
        "local_schema_corrector",
    )
    policy_allowed_review_policies: tuple[str, ...] = ("automatic", "on_disagreement", "always")
    voice_fusion_enabled: bool = True
    restricted_choice_hook_enabled: bool = False
    generative_judge_hook_enabled: bool = True
    codecompass_reranking_enabled: bool = False
    personalization_enabled: bool = True
    optional_models_enabled: bool = True
    audio_enhancement_enabled: bool = False
    enhancement_variants: tuple[str, ...] = ("original",)
    max_candidate_count: int = 8
    adaptive_routing_enabled: bool = True
    adaptive_max_total_latency_ms: int = 120_000
    adaptive_max_regional_rerun_ms: int = 30_000
    pyannote_manifest_path: str | None = None
    diarization_model_root: str | None = None

    @classmethod
    def from_env(cls) -> "VoiceRuntimeConfig":
        legacy_pipeline = _choice(
            os.getenv("VOICE_TRANSCRIPTION_PIPELINE"),
            default="simple",
            allowed=PIPELINES,
            name="VOICE_TRANSCRIPTION_PIPELINE",
        )
        configured_strategy = os.getenv("VOICE_RECOGNITION_STRATEGY") or os.getenv("VOICE_TRANSCRIPTION_MODE")
        strategy_aliases = {
            "classic_only": "single",
            "voice_model_only": "single",
            "adaptive_local": "single",
            "manual_review": "single",
        }
        recognition_strategy = strategy_aliases.get(
            str(configured_strategy or "").strip().lower(), str(configured_strategy or "single").strip().lower()
        )
        transport_default = "stream" if legacy_pipeline == "realtime_streaming" else "batch"
        raw_legacy_fallback_order = os.getenv("VOICE_BACKEND_FALLBACK_ORDER")
        legacy_fallback_order = _csv_tuple(
            raw_legacy_fallback_order,
            default=("voxtral", "mock"),
        )
        explicit_primary_backend = str(
            os.getenv("VOICE_PRIMARY_BACKEND") or os.getenv("VOICE_ASR_BACKEND") or ""
        ).strip().lower()
        projected_primary_backend = explicit_primary_backend
        if not projected_primary_backend and legacy_pipeline == "whisper_cpp":
            projected_primary_backend = "whisper_cpp"
        if not projected_primary_backend and raw_legacy_fallback_order is not None:
            projected_primary_backend = legacy_fallback_order[0]
        projected_primary_backend = projected_primary_backend or "mock"
        raw_secondary_backends = os.getenv("VOICE_SECONDARY_BACKENDS")
        if raw_secondary_backends is None and raw_legacy_fallback_order is not None:
            projected_secondary_backends = tuple(
                backend for backend in legacy_fallback_order if backend != projected_primary_backend
            )
        else:
            projected_secondary_backends = tuple(
                item.strip().lower()
                for item in str(raw_secondary_backends or "").split(",")
                if item.strip()
            )
        config = cls(
            host=os.getenv("VOICE_RUNTIME_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_as_int(os.getenv("VOICE_RUNTIME_PORT"), 8090),
            provider=os.getenv("VOICE_PROVIDER", "voice-runtime").strip() or "voice-runtime",
            backend=os.getenv("VOICE_RUNTIME_BACKEND", "mock").strip() or "mock",
            model=os.getenv("VOICE_MODEL", "voxtral").strip() or "voxtral",
            fallback_model=os.getenv("VOICE_FALLBACK_MODEL", "whisper-small").strip() or "whisper-small",
            timeout_sec=max(1, _as_int(os.getenv("VOICE_TIMEOUT_SEC"), 120)),
            max_audio_mb=max(1, _as_int(os.getenv("VOICE_MAX_AUDIO_MB"), 25)),
            enable_streaming=_as_bool(os.getenv("VOICE_ENABLE_STREAMING"), False),
            store_audio=_as_bool(os.getenv("VOICE_STORE_AUDIO"), False),
            device=os.getenv("VOICE_RUNTIME_DEVICE", "auto").strip() or "auto",
            model_path=(os.getenv("VOICE_RUNTIME_MODEL_PATH", "").strip() or None),
            voxtral_runner_path=os.getenv("VOICE_VOXTRAL_RUNNER_PATH", "").strip() or None,
            voxtral_runner_style=os.getenv("VOICE_VOXTRAL_RUNNER_STYLE", "realtime").strip().lower() or "realtime",
            backend_fallback_order=legacy_fallback_order,
            transcription_pipeline=legacy_pipeline,
            vad_backend=_choice(
                os.getenv("VOICE_VAD_BACKEND"),
                default="mock",
                allowed=VAD_BACKENDS,
                name="VOICE_VAD_BACKEND",
            ),
            silero_vad_model_path=os.getenv("VOICE_SILERO_VAD_MODEL_PATH", "").strip() or None,
            silero_vad_threshold=_as_float(os.getenv("VOICE_SILERO_VAD_THRESHOLD"), 0.5),
            asr_backend=_choice(
                os.getenv("VOICE_ASR_BACKEND"),
                default="mock",
                allowed=ASR_BACKENDS,
                name="VOICE_ASR_BACKEND",
            ),
            postprocess_backend=_choice(
                os.getenv("VOICE_POSTPROCESS_BACKEND"),
                default="none",
                allowed=POSTPROCESS_BACKENDS,
                name="VOICE_POSTPROCESS_BACKEND",
            ),
            confidence_rerun_enabled=_as_bool(os.getenv("VOICE_CONFIDENCE_RERUN_ENABLED"), False),
            confidence_threshold=max(0.0, min(1.0, _as_float(os.getenv("VOICE_CONFIDENCE_THRESHOLD"), 0.7))),
            rerun_backend=os.getenv("VOICE_RERUN_BACKEND", "mock").strip().lower() or "mock",
            rerun_max_segments=max(0, _as_int(os.getenv("VOICE_RERUN_MAX_SEGMENTS"), 3)),
            rerun_max_audio_ms=max(
                0,
                _as_int(os.getenv("VOICE_RERUN_MAX_AUDIO_MS"), 30_000),
            ),
            diarization_backend=_choice(
                os.getenv("VOICE_DIARIZATION_BACKEND"),
                default="none",
                allowed=DIARIZATION_BACKENDS,
                name="VOICE_DIARIZATION_BACKEND",
            ),
            glossary_path=os.getenv("VOICE_GLOSSARY_PATH", "").strip() or None,
            vosk_model_path=(
                os.getenv("VOICE_VOSK_MODEL_PATH", "").strip()
                or os.getenv("VOICE_RUNTIME_MODEL_PATH", "").strip()
                or None
            ),
            whisper_cpp_bin=os.getenv("VOICE_WHISPER_CPP_BIN", "").strip() or None,
            whisper_cpp_model_path=os.getenv("VOICE_WHISPER_CPP_MODEL_PATH", "").strip() or None,
            whisper_cpp_extra_args=tuple(
                item.strip() for item in os.getenv("VOICE_WHISPER_CPP_EXTRA_ARGS", "").split() if item.strip()
            ),
            whisper_cpp_threads=max(1, _as_int(os.getenv("VOICE_WHISPER_CPP_THREADS"), 4)),
            whisper_cpp_gpu_layers=max(0, _as_int(os.getenv("VOICE_WHISPER_CPP_GPU_LAYERS"), 0)),
            whisper_cpp_beam_size=max(1, _as_int(os.getenv("VOICE_WHISPER_CPP_BEAM_SIZE"), 5)),
            whisper_cpp_temperature=_as_float(os.getenv("VOICE_WHISPER_CPP_TEMPERATURE"), 0.0),
            whisper_cpp_prompt_max_chars=max(0, _as_int(os.getenv("VOICE_WHISPER_CPP_PROMPT_MAX_CHARS"), 512)),
            faster_whisper_model_path=os.getenv("VOICE_FASTER_WHISPER_MODEL_PATH", "").strip() or None,
            faster_whisper_compute_type=_choice(
                os.getenv("VOICE_FASTER_WHISPER_COMPUTE_TYPE"),
                default="default",
                allowed=FASTER_WHISPER_COMPUTE_TYPES,
                name="VOICE_FASTER_WHISPER_COMPUTE_TYPE",
            ),
            faster_whisper_beam_size=max(1, _as_int(os.getenv("VOICE_FASTER_WHISPER_BEAM_SIZE"), 5)),
            faster_whisper_vad_filter=_as_bool(os.getenv("VOICE_FASTER_WHISPER_VAD_FILTER"), False),
            faster_whisper_vad_min_silence_ms=max(
                0,
                _as_int(os.getenv("VOICE_FASTER_WHISPER_VAD_MIN_SILENCE_MS"), 500),
            ),
            transport_mode=_choice(
                os.getenv("VOICE_TRANSPORT_MODE"),
                default=transport_default,
                allowed=TRANSPORT_MODES,
                name="VOICE_TRANSPORT_MODE",
            ),
            recognition_strategy=_choice(
                recognition_strategy,
                default="single",
                allowed=RECOGNITION_STRATEGIES,
                name="VOICE_RECOGNITION_STRATEGY",
            ),
            routing_strategy=_choice(
                os.getenv("VOICE_ROUTING_STRATEGY"),
                default="fixed",
                allowed=ROUTING_STRATEGIES,
                name="VOICE_ROUTING_STRATEGY",
            ),
            correction_policy=_choice(
                os.getenv("VOICE_CORRECTION_POLICY"),
                default="none",
                allowed=CORRECTION_POLICIES,
                name="VOICE_CORRECTION_POLICY",
            ),
            review_policy=_choice(
                os.getenv("VOICE_REVIEW_POLICY"),
                default="on_disagreement"
                if str(configured_strategy or "").strip().lower() == "manual_review"
                else "automatic",
                allowed=REVIEW_POLICIES,
                name="VOICE_REVIEW_POLICY",
            ),
            primary_backend=projected_primary_backend,
            secondary_backends=projected_secondary_backends,
            max_parallel_backends=max(1, _as_int(os.getenv("VOICE_MAX_PARALLEL_BACKENDS"), 2)),
            candidate_deadline_sec=max(0.1, _as_float(os.getenv("VOICE_CANDIDATE_DEADLINE_SEC"), 120.0)),
            max_audio_duration_sec=max(1, _as_int(os.getenv("VOICE_MAX_AUDIO_DURATION_SEC"), 3600)),
            max_decoded_pcm_mb=max(1, _as_int(os.getenv("VOICE_MAX_DECODED_PCM_MB"), 256)),
            max_queue_depth=max(1, _as_int(os.getenv("VOICE_MAX_QUEUE_DEPTH"), 16)),
            resource_max_ram_mb=max(
                1,
                _as_int(os.getenv("VOICE_RESOURCE_MAX_RAM_MB"), 16_384),
            ),
            resource_max_vram_mb=max(
                0,
                _as_int(os.getenv("VOICE_RESOURCE_MAX_VRAM_MB"), 24_576),
            ),
            resource_max_concurrent_backends=max(
                1,
                _as_int(os.getenv("VOICE_RESOURCE_MAX_CONCURRENT_BACKENDS"), 8),
            ),
            resource_max_audio_seconds=max(
                1,
                _as_int(
                    os.getenv("VOICE_RESOURCE_MAX_AUDIO_SECONDS"),
                    max(1, _as_int(os.getenv("VOICE_MAX_AUDIO_DURATION_SEC"), 3_600)),
                ),
            ),
            resource_max_queue_depth=max(
                1,
                _as_int(os.getenv("VOICE_RESOURCE_MAX_QUEUE_DEPTH"), 16),
            ),
            production_profile=_as_bool(os.getenv("VOICE_PRODUCTION_PROFILE"), False),
            allow_model_download=_as_bool(os.getenv("VOICE_ALLOW_MODEL_DOWNLOAD"), False),
            model_manifest_path=os.getenv("VOICE_MODEL_MANIFEST_PATH", "").strip() or None,
            model_root=os.getenv("VOICE_MODEL_ROOT", "").strip() or None,
            calibration_path=os.getenv("VOICE_CALIBRATION_PATH", "").strip() or None,
            internal_service_token=os.getenv("VOICE_INTERNAL_SERVICE_TOKEN", "").strip() or None,
            policy_allowed_backends=_csv_tuple(
                os.getenv("VOICE_POLICY_ALLOWED_BACKENDS"),
                default=("vosk", "whisper_cpp", "faster_whisper", "voxtral"),
            ),
            policy_allowed_recognition_strategies=_csv_tuple(
                os.getenv("VOICE_POLICY_ALLOWED_RECOGNITION_STRATEGIES"),
                default=("single", "parallel_compare", "classic_then_correct"),
            ),
            policy_allowed_routing_strategies=_csv_tuple(
                os.getenv("VOICE_POLICY_ALLOWED_ROUTING_STRATEGIES"),
                default=("fixed", "adaptive_local"),
            ),
            policy_allowed_correction_policies=_csv_tuple(
                os.getenv("VOICE_POLICY_ALLOWED_CORRECTION_POLICIES"),
                default=("none", "rules", "restricted_choice", "local_schema_corrector"),
            ),
            policy_allowed_review_policies=_csv_tuple(
                os.getenv("VOICE_POLICY_ALLOWED_REVIEW_POLICIES"),
                default=("automatic", "on_disagreement", "always"),
            ),
            voice_fusion_enabled=_as_bool(os.getenv("VOICE_FUSION_ENABLED"), True),
            restricted_choice_hook_enabled=_as_bool(os.getenv("VOICE_RESTRICTED_CHOICE_HOOK_ENABLED"), False),
            generative_judge_hook_enabled=_as_bool(os.getenv("VOICE_GENERATIVE_JUDGE_HOOK_ENABLED"), True),
            codecompass_reranking_enabled=_as_bool(os.getenv("VOICE_CODECOMPASS_RERANKING_ENABLED"), False),
            personalization_enabled=_as_bool(os.getenv("VOICE_PERSONALIZATION_ENABLED"), True),
            optional_models_enabled=_as_bool(os.getenv("VOICE_OPTIONAL_MODELS_ENABLED"), True),
            audio_enhancement_enabled=_as_bool(os.getenv("VOICE_AUDIO_ENHANCEMENT_ENABLED"), False),
            enhancement_variants=_csv_tuple(
                os.getenv("VOICE_ENHANCEMENT_VARIANTS"),
                default=("original",),
            ),
            max_candidate_count=max(1, _as_int(os.getenv("VOICE_MAX_CANDIDATE_COUNT"), 8)),
            adaptive_routing_enabled=_as_bool(os.getenv("VOICE_ADAPTIVE_ROUTING_ENABLED"), True),
            adaptive_max_total_latency_ms=max(
                1,
                _as_int(os.getenv("VOICE_ADAPTIVE_MAX_TOTAL_LATENCY_MS"), 120_000),
            ),
            adaptive_max_regional_rerun_ms=max(
                0,
                _as_int(os.getenv("VOICE_ADAPTIVE_MAX_REGIONAL_RERUN_MS"), 30_000),
            ),
            pyannote_manifest_path=os.getenv("VOICE_PYANNOTE_MANIFEST_PATH", "").strip() or None,
            diarization_model_root=os.getenv("VOICE_DIARIZATION_MODEL_ROOT", "").strip() or None,
        )
        config.validate()
        return config

    def validate(self) -> None:
        for field_name, value, allowed in (
            ("VOICE_TRANSCRIPTION_PIPELINE", self.transcription_pipeline, PIPELINES),
            ("VOICE_VAD_BACKEND", self.vad_backend, VAD_BACKENDS),
            ("VOICE_POSTPROCESS_BACKEND", self.postprocess_backend, POSTPROCESS_BACKENDS),
            ("VOICE_DIARIZATION_BACKEND", self.diarization_backend, DIARIZATION_BACKENDS),
            ("VOICE_TRANSPORT_MODE", self.transport_mode, TRANSPORT_MODES),
            ("VOICE_RECOGNITION_STRATEGY", self.recognition_strategy, RECOGNITION_STRATEGIES),
            ("VOICE_ROUTING_STRATEGY", self.routing_strategy, ROUTING_STRATEGIES),
            ("VOICE_CORRECTION_POLICY", self.correction_policy, CORRECTION_POLICIES),
            ("VOICE_REVIEW_POLICY", self.review_policy, REVIEW_POLICIES),
        ):
            if value not in allowed:
                raise ValueError(f"unsupported {field_name}: {value}; allowed={sorted(allowed)}")
        backend_fields = {
            "VOICE_PRIMARY_BACKEND": self.primary_backend,
            "VOICE_ASR_BACKEND": self.asr_backend,
            "VOICE_RERUN_BACKEND": self.rerun_backend,
        }
        for field_name, value in backend_fields.items():
            if value not in ASR_BACKENDS:
                raise ValueError(f"unsupported {field_name}: {value}; allowed={sorted(ASR_BACKENDS)}")
        invalid_secondary = sorted(set(self.secondary_backends) - ASR_BACKENDS)
        if invalid_secondary:
            raise ValueError(
                f"unsupported VOICE_SECONDARY_BACKENDS: {invalid_secondary}; allowed={sorted(ASR_BACKENDS)}"
            )
        invalid_fallback = sorted(set(self.backend_fallback_order) - ASR_BACKENDS)
        if invalid_fallback:
            raise ValueError(
                f"unsupported VOICE_BACKEND_FALLBACK_ORDER: {invalid_fallback}; allowed={sorted(ASR_BACKENDS)}"
            )
        _validate_unique_subset(
            "VOICE_POLICY_ALLOWED_BACKENDS",
            self.policy_allowed_backends,
            ASR_BACKENDS - {"mock"},
        )
        _validate_unique_subset(
            "VOICE_POLICY_ALLOWED_RECOGNITION_STRATEGIES",
            self.policy_allowed_recognition_strategies,
            RECOGNITION_STRATEGIES,
        )
        _validate_unique_subset(
            "VOICE_POLICY_ALLOWED_ROUTING_STRATEGIES",
            self.policy_allowed_routing_strategies,
            ROUTING_STRATEGIES,
        )
        _validate_unique_subset(
            "VOICE_POLICY_ALLOWED_CORRECTION_POLICIES",
            self.policy_allowed_correction_policies,
            CORRECTION_POLICIES,
        )
        _validate_unique_subset(
            "VOICE_POLICY_ALLOWED_REVIEW_POLICIES",
            self.policy_allowed_review_policies,
            REVIEW_POLICIES,
        )
        _validate_unique_subset("VOICE_ENHANCEMENT_VARIANTS", self.enhancement_variants, ENHANCEMENT_VARIANTS)
        if not self.enhancement_variants or self.enhancement_variants[0] != "original":
            raise ValueError("VOICE_ENHANCEMENT_VARIANTS must start with original")
        if len(self.enhancement_variants) > 4:
            raise ValueError("VOICE_ENHANCEMENT_VARIANTS exceeds the variant budget")
        if len(self.enhancement_variants) > 1 and not self.audio_enhancement_enabled:
            raise ValueError("non-original enhancement variants require VOICE_AUDIO_ENHANCEMENT_ENABLED")
        if not 1 <= self.max_candidate_count <= 32:
            raise ValueError("VOICE_MAX_CANDIDATE_COUNT must be between 1 and 32")
        if not 0.0 <= self.silero_vad_threshold <= 1.0:
            raise ValueError("VOICE_SILERO_VAD_THRESHOLD must be between 0 and 1")
        if not 1 <= self.max_parallel_backends <= 32:
            raise ValueError("VOICE_MAX_PARALLEL_BACKENDS must be between 1 and 32")
        if not 0.1 <= self.candidate_deadline_sec <= 3600:
            raise ValueError("VOICE_CANDIDATE_DEADLINE_SEC must be between 0.1 and 3600")
        if self.adaptive_max_total_latency_ms <= 0 or self.adaptive_max_regional_rerun_ms < 0:
            raise ValueError("adaptive routing budgets are invalid")
        if self.rerun_max_audio_ms < 0:
            raise ValueError("VOICE_RERUN_MAX_AUDIO_MS cannot be negative")
        if (
            self.resource_max_ram_mb <= 0
            or self.resource_max_vram_mb < 0
            or self.resource_max_concurrent_backends <= 0
            or self.resource_max_audio_seconds <= 0
            or self.resource_max_queue_depth <= 0
        ):
            raise ValueError("voice resource admission budgets are invalid")
        if not 1 <= self.whisper_cpp_threads <= 256:
            raise ValueError("VOICE_WHISPER_CPP_THREADS must be between 1 and 256")
        if not 0 <= self.whisper_cpp_gpu_layers <= 512:
            raise ValueError("VOICE_WHISPER_CPP_GPU_LAYERS must be between 0 and 512")
        if not 1 <= self.whisper_cpp_beam_size <= 20:
            raise ValueError("VOICE_WHISPER_CPP_BEAM_SIZE must be between 1 and 20")
        if not 0 <= self.whisper_cpp_temperature <= 2:
            raise ValueError("VOICE_WHISPER_CPP_TEMPERATURE must be between 0 and 2")
        if not 0 <= self.whisper_cpp_prompt_max_chars <= 8_000:
            raise ValueError("VOICE_WHISPER_CPP_PROMPT_MAX_CHARS must be between 0 and 8000")
        if not 1 <= self.faster_whisper_beam_size <= 20:
            raise ValueError("VOICE_FASTER_WHISPER_BEAM_SIZE must be between 1 and 20")
        if self.faster_whisper_compute_type not in FASTER_WHISPER_COMPUTE_TYPES:
            raise ValueError("VOICE_FASTER_WHISPER_COMPUTE_TYPE is invalid")
        if not 0 <= self.faster_whisper_vad_min_silence_ms <= 10_000:
            raise ValueError("VOICE_FASTER_WHISPER_VAD_MIN_SILENCE_MS must be between 0 and 10000")
        if self.diarization_backend == "pyannote" and (
            not self.pyannote_manifest_path or not self.diarization_model_root
        ):
            raise ValueError("pyannote diarization requires pinned manifest and model root paths")
        if self.production_profile:
            self._validate_production()

    def _validate_production(self) -> None:
        selected = {
            self.primary_backend,
            self.asr_backend,
            self.rerun_backend,
            *self.secondary_backends,
            *self.backend_fallback_order,
            *self.policy_allowed_backends,
        }
        if "mock" in selected:
            raise ValueError("mock voice backends are forbidden in production profiles")
        if "voxtral" in selected and (not self.model_path or not self.voxtral_runner_path):
            raise ValueError("production Voxtral requires local model and runner paths")
        if self.vad_backend == "silero" and not self.silero_vad_model_path:
            raise ValueError("production Silero VAD requires VOICE_SILERO_VAD_MODEL_PATH")
        if self.recognition_strategy in {"parallel_compare", "parallel_fusion"} and not self.calibration_path:
            raise ValueError("production parallel fusion requires VOICE_CALIBRATION_PATH")
        if (
            self.voice_fusion_enabled
            and "parallel_compare" in self.policy_allowed_recognition_strategies
            and not self.calibration_path
        ):
            raise ValueError("production Hub-enabled voice fusion requires VOICE_CALIBRATION_PATH")
        if self.whisper_cpp_extra_args:
            raise ValueError("VOICE_WHISPER_CPP_EXTRA_ARGS is forbidden in production profiles")
        if self.store_audio:
            raise ValueError(
                "VOICE_STORE_AUDIO requires a hub-issued retention envelope and cannot be enabled statically"
            )
        if not self.internal_service_token or len(self.internal_service_token) < 24:
            raise ValueError("VOICE_INTERNAL_SERVICE_TOKEN with at least 24 characters is required in production")


def _validate_unique_subset(name: str, values: tuple[str, ...], allowed: frozenset[str]) -> None:
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique values")
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(f"unsupported {name}: {invalid}; allowed={sorted(allowed)}")
