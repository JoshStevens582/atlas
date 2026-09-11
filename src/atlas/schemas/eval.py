from pydantic import BaseModel, Field, model_validator


class GoldenQuestion(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    source_title_contains: list[str] = Field(default_factory=list)
    chunk_contains: list[str] = Field(default_factory=list)
    expect_unknown: bool = False

    @model_validator(mode="after")
    def require_retrieval_targets(self) -> "GoldenQuestion":
        if not self.expect_unknown and not self.chunk_contains:
            raise ValueError(
                f"Question '{self.id}' needs chunk_contains or expect_unknown=true."
            )
        return self


class GoldenQuestionSet(BaseModel):
    questions: list[GoldenQuestion] = Field(min_length=1)


class CaseScore(BaseModel):
    question_id: str
    question: str
    answer: str
    retrieved_titles: list[str]
    retrieval_hit: bool | None
    generation_pass: bool
    case_pass: bool
    notes: list[str]


class EvalReport(BaseModel):
    indexed_document_count: int
    cases: list[CaseScore]
