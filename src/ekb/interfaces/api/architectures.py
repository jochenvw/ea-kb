from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from ekb.domain.architectures import (
    ArchitectureRequirementDetail,
    SolutionArchitecture,
)
from ekb.domain.assessment import (
    RetainedAssessment,
    SolutionAssessment,
    SolutionAssessmentInput,
)
from ekb.domain.baselines import BaselineSummary
from ekb.domain.portfolio import (
    ChangeImpactInput,
    ChangeImpactReport,
    ExceptionRiskInput,
    ExceptionRiskReport,
)
from ekb.runtime import Runtime


def register(app: FastAPI, active: Callable[[Request], Runtime]) -> None:
    @app.get("/api/architectures", response_model=list[SolutionArchitecture])
    async def architectures(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        after: UUID | None = None,
    ):
        return await active(request).architectures.list(limit, after=after)

    @app.get(
        "/api/architectures/{architecture_id}",
        response_model=SolutionArchitecture,
    )
    async def architecture(request: Request, architecture_id: UUID):
        result = await active(request).architectures.get(architecture_id)
        if result is None:
            raise HTTPException(404, "Solution architecture not found")
        return result

    @app.get("/api/architectures/{architecture_id}/revisions/{revision_id}/artifact")
    async def architecture_artifact(
        request: Request, architecture_id: UUID, revision_id: UUID
    ) -> Response:
        try:
            revision, content = await active(request).architectures.artifact(
                architecture_id, revision_id
            )
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Solution architecture artifact not found") from None
        return Response(
            content,
            media_type=revision.media_type,
            headers={"Content-Disposition": f'attachment; filename="{revision.artifact_name}"'},
        )

    @app.get(
        "/api/architectures/{architecture_id}/requirements",
        response_model=tuple[ArchitectureRequirementDetail, ...],
    )
    async def architecture_requirements(
        request: Request,
        architecture_id: UUID,
        version: str | None = None,
    ):
        try:
            return await active(request).architectures.requirements(
                architecture_id, version=version
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get(
        "/api/architectures/{architecture_id}/assessments",
        response_model=list[RetainedAssessment],
    )
    async def architecture_assessments(
        request: Request,
        architecture_id: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ):
        return await active(request).architectures.assessments(architecture_id, limit)

    @app.get("/api/baselines", response_model=list[BaselineSummary])
    async def baselines(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        return await active(request).baselines.list(limit)

    @app.post("/api/assessments", response_model=SolutionAssessment)
    async def assess_solution(request: Request, body: SolutionAssessmentInput):
        try:
            return await active(request).assessments.assess(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post("/api/portfolio/exception-risk", response_model=ExceptionRiskReport)
    async def exception_risk(request: Request, body: ExceptionRiskInput):
        try:
            return await active(request).portfolio.exception_risk(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post("/api/portfolio/change-impact", response_model=ChangeImpactReport)
    async def change_impact(request: Request, body: ChangeImpactInput):
        try:
            return await active(request).portfolio.change_impact(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
