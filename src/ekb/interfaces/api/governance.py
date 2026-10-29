import secrets
from collections.abc import Callable
from datetime import date
from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import Field

from ekb.domain.issues import Issue, IssueDetail, IssueStatus
from ekb.domain.models import Model, Scope
from ekb.domain.resolution import Decision, Finding, Resolution, ResolutionProfile
from ekb.runtime import Runtime


class CanonicalProposal(Model):
    concept_id: UUID
    version_id: UUID
    scope: Scope = Field(default_factory=Scope)
    allow_prerelease: bool = False


class IssueReview(Model):
    status: IssueStatus
    rationale: str = Field(min_length=1)
    owner: str | None = None


def register(
    app: FastAPI,
    active: Callable[[Request], Runtime],
    templates: Jinja2Templates,
) -> None:
    basic = HTTPBasic(auto_error=False)

    def authorize(
        request: Request,
        credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    ) -> str:
        try:
            loopback = request.client is not None and ip_address(request.client.host).is_loopback
        except ValueError:
            loopback = False
        if request.url.scheme != "https" and not loopback:
            raise HTTPException(403, "Governance requires HTTPS or a loopback connection")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin governance is forbidden")
        token = active(request).settings.governance_token.get_secret_value()
        if not token:
            raise HTTPException(403, "HTTP governance disabled; configure EKB_GOVERNANCE_TOKEN")
        if (
            credentials is None
            or not credentials.username.strip()
            or not secrets.compare_digest(credentials.password.encode(), token.encode())
        ):
            raise HTTPException(
                401,
                "Operator authentication required",
                headers={"WWW-Authenticate": 'Basic realm="EKB local governance"'},
            )
        return credentials.username

    @app.get("/api/knowledge/{concept_id}/resolve", response_model=Resolution)
    async def resolve(
        request: Request,
        concept_id: UUID,
        profile: ResolutionProfile = "canonical",
        scope: str = "{}",
        as_of: date | None = None,
    ):
        try:
            return await active(request).resolver.resolve_concept(
                concept_id, Scope.model_validate_json(scope), profile, as_of
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/knowledge/search", response_model=tuple[Resolution, ...])
    async def search(
        request: Request,
        q: str,
        profile: ResolutionProfile = "canonical",
        scope: str = "{}",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        try:
            return await active(request).resolver.resolve_requirement(
                q, Scope.model_validate_json(scope), profile, limit=limit
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/reconciliation/findings", response_model=list[Finding])
    async def findings(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        check_id: str | None = None,
        after: UUID | None = None,
    ):
        return await active(request).reconciliation.findings(limit, check_id=check_id, after=after)

    @app.get("/api/reconciliation/decisions", response_model=list[Decision])
    async def decisions(request: Request, finding_id: UUID | None = None):
        return await active(request).reconciliation.history(finding_id)

    @app.get("/api/reconciliation/findings/{finding_id}", response_model=Finding)
    async def finding_detail(request: Request, finding_id: UUID):
        result = await active(request).reconciliation.get(finding_id)
        if result is None:
            raise HTTPException(404, "Finding not found")
        return result

    @app.post("/api/reconciliation/decisions", response_model=Decision)
    async def decide(
        request: Request,
        decision: Decision,
        actor: Annotated[str, Depends(authorize)],
    ):
        try:
            return await active(request).reconciliation.decide(
                decision.model_copy(
                    update={
                        "actor": actor,
                        "actor_kind": "human",
                        "policy": None,
                    }
                )
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/reconciliation/canonical", response_model=Finding)
    async def canonical(
        request: Request,
        proposal: CanonicalProposal,
        actor: Annotated[str, Depends(authorize)],
    ):
        try:
            return await active(request).reconciliation.propose_canonical(
                proposal.concept_id,
                proposal.version_id,
                proposal.scope,
                proposal.allow_prerelease,
                actor,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/issues", response_model=list[Issue])
    async def issues(
        request: Request,
        status: IssueStatus | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        scope: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ):
        try:
            return await active(request).issues.list(
                status,
                issue_type,
                severity,
                limit,
                Scope.model_validate_json(scope) if scope else None,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/issues/{issue_id}", response_model=IssueDetail)
    async def issue_detail(request: Request, issue_id: UUID):
        detail = await active(request).issues.get(issue_id)
        if detail is None:
            raise HTTPException(404, "Issue not found")
        return detail

    @app.post("/api/issues/{issue_id}/review", response_model=IssueDetail)
    async def review(
        request: Request,
        issue_id: UUID,
        body: IssueReview,
        actor: Annotated[str, Depends(authorize)],
    ):
        try:
            return await active(request).issues.review(
                issue_id, body.status, actor, body.rationale, body.owner
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/pipeline/status")
    async def pipeline_status(request: Request, run_id: UUID | None = None):
        return await active(request).orchestrator().status(run_id)

    @app.get("/review", response_class=HTMLResponse)
    async def review_page(
        request: Request,
        status: IssueStatus | None = None,
        finding_id: UUID | None = None,
    ):
        runtime = active(request)
        proposals = await runtime.reconciliation.findings(200)
        if finding_id:
            selected = await runtime.reconciliation.get(finding_id)
            if selected:
                proposals = [selected]
        history = await runtime.reconciliation.history()
        heads = {str(d.finding_id): d for d in history}
        concept_ids = list(dict.fromkeys(cid for f in proposals for cid in f.concept_ids))
        concepts = {str(cid): await runtime.knowledge.get(cid) for cid in concept_ids[:200]}
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "issues": await runtime.issues.list(status=status),
                "findings": proposals,
                "decisions": history,
                "heads": heads,
                "concepts": concepts,
            },
        )

    @app.post("/review/decisions")
    async def review_decision(
        request: Request,
        actor: Annotated[str, Depends(authorize)],
        finding_id: Annotated[UUID, Form()],
        outcome: Annotated[str, Form()],
        rationale: Annotated[str, Form()],
        previous_id: Annotated[str, Form()] = "",
    ):
        try:
            decision = Decision.model_validate(
                dict(
                    finding_id=finding_id,
                    outcome=outcome,
                    actor=actor,
                    rationale=rationale,
                    previous_id=UUID(previous_id) if previous_id else None,
                )
            )
            await active(request).reconciliation.decide(decision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/issues")
    async def review_issue(
        request: Request,
        actor: Annotated[str, Depends(authorize)],
        issue_id: Annotated[UUID, Form()],
        status: Annotated[IssueStatus, Form()],
        rationale: Annotated[str, Form()],
    ):
        await active(request).issues.review(issue_id, status, actor, rationale)
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/canonical")
    async def review_canonical(
        request: Request,
        actor: Annotated[str, Depends(authorize)],
        concept_id: Annotated[UUID, Form()],
        version_id: Annotated[UUID, Form()],
        scope: Annotated[str, Form()] = "{}",
        allow_prerelease: Annotated[bool, Form()] = False,
    ):
        try:
            await active(request).reconciliation.propose_canonical(
                concept_id,
                version_id,
                Scope.model_validate_json(scope),
                allow_prerelease,
                actor,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return RedirectResponse("/review", status_code=303)
