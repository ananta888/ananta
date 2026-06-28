from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OWNERSHIP_POLICIES = {"owned", "borrowed", "clone_required", "lifetime_unknown"}

RUST_ERROR_POLICIES = {"result_t_e", "anyhow", "custom_enum", "needs_review", "panic_not_allowed"}


@dataclass
class OwnershipDecision:
    field_or_param: str
    policy: str
    reason: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"field_or_param": self.field_or_param, "policy": self.policy, "reason": self.reason, "warnings": self.warnings}


@dataclass
class RustErrorPolicy:
    python_exception: str
    rust_policy: str
    rust_error_type: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"python_exception": self.python_exception, "rust_policy": self.rust_policy, "rust_error_type": self.rust_error_type, "warnings": self.warnings}


class RustOwnershipPolicyEngine:
    """
    Conservative v1 ownership policy:
    - All struct fields are owned by default.
    - Function parameters use owned values unless annotated for borrow.
    - Mutable Python types produce warnings.
    - Complex reference relationships produce lifetime_unknown → needs_review.
    """

    def decide_field_ownership(self, field_name: str, rust_type: str, *, is_mutable: bool = False) -> OwnershipDecision:
        warnings = []
        if is_mutable:
            warnings.append(f"mutable_field_{field_name}: consider mut or Arc<Mutex<T>> policy")
        if rust_type.startswith("&"):
            return OwnershipDecision(field_name, "lifetime_unknown", "reference type in struct requires explicit lifetime", warnings + ["struct_with_reference_requires_lifetime_annotation"])
        if "Arc<" in rust_type or "Rc<" in rust_type:
            return OwnershipDecision(field_name, "clone_required", "reference-counted type — clone on field access", warnings)
        return OwnershipDecision(field_name, "owned", "owned value — default v1 policy", warnings)

    def decide_param_ownership(self, param_name: str, rust_type: str, *, is_mutable: bool = False) -> OwnershipDecision:
        warnings = []
        if is_mutable:
            warnings.append(f"mutable_param_{param_name}: use &mut T policy")
            return OwnershipDecision(param_name, "borrowed", "&mut reference for mutable parameter", warnings)
        if rust_type.startswith("String"):
            return OwnershipDecision(param_name, "borrowed", "use &str for string parameters — more ergonomic", [f"prefer_str_ref_for_{param_name}"])
        if rust_type.startswith("Vec<"):
            return OwnershipDecision(param_name, "borrowed", "use &[T] slice for Vec parameters", [f"prefer_slice_for_{param_name}"])
        return OwnershipDecision(param_name, "owned", "owned parameter — safe default", warnings)

    def classify_exception_policy(self, python_exception: str) -> RustErrorPolicy:
        exc_lower = python_exception.lower()
        if python_exception in ("", "Exception", "BaseException"):
            return RustErrorPolicy(python_exception, "needs_review", "Box<dyn std::error::Error>", ["bare_exception_blocks_auto_transform"])
        if exc_lower in ("valueerror", "typeerror", "keyerror", "indexerror", "attributeerror", "runtimeerror", "notimplementederror"):
            return RustErrorPolicy(python_exception, "result_t_e", f"{python_exception}Error", [])
        if exc_lower in ("ioerror", "oserror", "filenotfounderror", "permissionerror"):
            return RustErrorPolicy(python_exception, "result_t_e", "std::io::Error", [])
        if exc_lower in ("connectionerror", "timeouterror", "urlerror"):
            return RustErrorPolicy(python_exception, "result_t_e", "Box<dyn std::error::Error>", ["network_error_consider_custom_type"])
        return RustErrorPolicy(python_exception, "needs_review", "Box<dyn std::error::Error>", [f"unknown_exception_type:{python_exception}"])
