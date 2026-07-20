from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

FieldType = Literal["text", "checkbox", "dropdown"]
ProjectMode = Literal["install", "upgrade"]
AnswerType = Literal["text", "single_choice", "multi_choice"]


class TemplateField(BaseModel):
    id: str
    answer_id: str
    word_index: int = Field(ge=1)
    type: FieldType
    label: str
    option_value: str | None = None
    applies_to: list[ProjectMode] = Field(default_factory=lambda: ["install", "upgrade"])

    @model_validator(mode="after")
    def validate_option_value(self) -> "TemplateField":
        if self.type == "checkbox" and not self.option_value:
            raise ValueError(f"Checkbox field {self.id!r} requires option_value")
        if self.type != "checkbox" and self.option_value is not None:
            raise ValueError(f"Only checkbox fields may define option_value: {self.id!r}")
        return self


class ProjectModeDefinition(BaseModel):
    id: ProjectMode
    label: str
    preset_values: dict[str, str | bool] = Field(default_factory=dict)


class AnswerDefinition(BaseModel):
    id: str
    label: str
    type: AnswerType
    choices: list[str] = Field(default_factory=list)
    applies_to: list[ProjectMode] = Field(default_factory=lambda: ["install", "upgrade"])
    extract: bool = True
    guidance: str | None = None

    @model_validator(mode="after")
    def validate_choices(self) -> "AnswerDefinition":
        if self.type == "text" and self.choices:
            raise ValueError(f"Text answer {self.id!r} cannot define choices")
        if self.type != "text" and not self.choices:
            raise ValueError(f"Choice answer {self.id!r} requires choices")
        if len(self.choices) != len(set(self.choices)):
            raise ValueError(f"Answer {self.id!r} contains duplicate choices")
        return self


class AnswerDerivationRule(BaseModel):
    id: str
    source_answer_id: str
    target_answer_id: str
    source_pattern: str
    target_value: str | list[str]

    @model_validator(mode="after")
    def validate_source_pattern(self) -> "AnswerDerivationRule":
        try:
            re.compile(self.source_pattern)
        except re.error as exc:
            raise ValueError(f"Derivation rule {self.id!r} has invalid source_pattern: {exc}") from exc
        return self


class ScopingTemplate(BaseModel):
    id: str
    name: str
    product: str
    version: str
    source_path: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_field_count: int = Field(ge=1)
    expected_type_counts: dict[FieldType, int]
    project_modes: list[ProjectModeDefinition]
    answers: list[AnswerDefinition]
    derivation_rules: list[AnswerDerivationRule] = Field(default_factory=list)
    fields: list[TemplateField]
    manifest_path: Path | None = Field(default=None, exclude=True)
    base_dir: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_field_layout(self) -> "ScopingTemplate":
        if len(self.fields) != self.expected_field_count:
            raise ValueError(
                f"Template {self.id!r} declares {self.expected_field_count} fields "
                f"but maps {len(self.fields)}"
            )

        ids = [field.id for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Template {self.id!r} contains duplicate field ids")

        indexes = sorted(field.word_index for field in self.fields)
        expected_indexes = list(range(1, self.expected_field_count + 1))
        if indexes != expected_indexes:
            raise ValueError(f"Template {self.id!r} must map each Word field index exactly once")

        actual_counts: dict[str, int] = {"text": 0, "checkbox": 0, "dropdown": 0}
        for field in self.fields:
            actual_counts[field.type] += 1
        if actual_counts != self.expected_type_counts:
            raise ValueError(
                f"Template {self.id!r} field type counts are {actual_counts}, "
                f"expected {self.expected_type_counts}"
            )

        mode_ids = [mode.id for mode in self.project_modes]
        if len(mode_ids) != len(set(mode_ids)):
            raise ValueError(f"Template {self.id!r} contains duplicate project modes")

        answer_ids = [answer.id for answer in self.answers]
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError(f"Template {self.id!r} contains duplicate answer ids")
        answers_by_id = {answer.id: answer for answer in self.answers}
        mapped_answer_ids = {field.answer_id for field in self.fields}
        if mapped_answer_ids != set(answers_by_id):
            missing = sorted(mapped_answer_ids - set(answers_by_id))
            unmapped = sorted(set(answers_by_id) - mapped_answer_ids)
            raise ValueError(
                f"Template {self.id!r} answer mapping mismatch; "
                f"missing definitions={missing}, answers without fields={unmapped}"
            )

        for field in self.fields:
            answer = answers_by_id[field.answer_id]
            if field.type == "text" and answer.type != "text":
                raise ValueError(f"Text field {field.id!r} must map to a text answer")
            if field.type == "dropdown" and answer.type != "single_choice":
                raise ValueError(f"Dropdown field {field.id!r} must map to a single-choice answer")
            if field.type == "checkbox":
                if answer.type not in {"single_choice", "multi_choice"}:
                    raise ValueError(f"Checkbox field {field.id!r} must map to a choice answer")
                if field.option_value not in answer.choices:
                    raise ValueError(
                        f"Checkbox field {field.id!r} option {field.option_value!r} "
                        f"is not declared by answer {answer.id!r}"
                    )

        rule_ids = [rule.id for rule in self.derivation_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError(f"Template {self.id!r} contains duplicate derivation rule ids")
        for rule in self.derivation_rules:
            if rule.source_answer_id not in answers_by_id or rule.target_answer_id not in answers_by_id:
                raise ValueError(f"Derivation rule {rule.id!r} references an unknown answer")
            source_answer = answers_by_id[rule.source_answer_id]
            target_answer = answers_by_id[rule.target_answer_id]
            if source_answer.type != "text":
                raise ValueError(f"Derivation rule {rule.id!r} source answer must be text")
            target_values = rule.target_value if isinstance(rule.target_value, list) else [rule.target_value]
            if target_answer.type == "text" or not target_values:
                raise ValueError(f"Derivation rule {rule.id!r} target answer must be a choice")
            if target_answer.type == "single_choice" and len(target_values) != 1:
                raise ValueError(f"Derivation rule {rule.id!r} must select one target value")
            if any(value not in target_answer.choices for value in target_values):
                raise ValueError(f"Derivation rule {rule.id!r} contains an invalid target value")
        return self

    def source_file(self) -> Path:
        if self.base_dir is None:
            raise RuntimeError(f"Template {self.id!r} is not attached to a catalog base directory")
        return (self.base_dir / self.source_path).resolve()

    def validate_source(self) -> Path:
        source = self.source_file()
        if not source.is_file():
            raise FileNotFoundError(f"Scoping template source not found: {source}")

        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != self.source_sha256:
            raise ValueError(
                f"Scoping template source changed for {self.id!r}: "
                f"expected SHA-256 {self.source_sha256}, found {actual_sha256}"
            )
        return source

    def mode(self, mode_id: ProjectMode) -> ProjectModeDefinition:
        for mode in self.project_modes:
            if mode.id == mode_id:
                return mode
        raise KeyError(f"Unknown project mode {mode_id!r} for template {self.id!r}")

    def field(self, field_id: str) -> TemplateField:
        for field in self.fields:
            if field.id == field_id:
                return field
        raise KeyError(f"Unknown field {field_id!r} for template {self.id!r}")

    def answer(self, answer_id: str) -> AnswerDefinition:
        for answer in self.answers:
            if answer.id == answer_id:
                return answer
        raise KeyError(f"Unknown answer {answer_id!r} for template {self.id!r}")

    def extractable_answers(self, mode: ProjectMode) -> list[AnswerDefinition]:
        self.mode(mode)
        return [answer for answer in self.answers if answer.extract and mode in answer.applies_to]
