from ekb.domain.execution import CheckOptions, PipelineConfiguration, StageOptions
from ekb.domain.models import fingerprint
from ekb.ports.workflow import PipelineStage


class StageRegistry:
    def __init__(self) -> None:
        self.stages: dict[str, PipelineStage] = {}

    def register(self, stage: PipelineStage) -> None:
        if stage.spec.id in self.stages:
            raise ValueError(f"Duplicate stage registration: {stage.spec.id}")
        ids = [check.spec.id for check in stage.checks]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate check registration")
        self.stages[stage.spec.id] = stage

    def order(self, selected: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(selected)) != len(selected) or set(selected) - self.stages.keys():
            raise ValueError("Selected stages must be unique registered identifiers")
        visiting, done, result = set(), set(), []

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise ValueError("Cyclic stage dependencies")
            if identifier in done:
                return
            visiting.add(identifier)
            for dependency in self.stages[identifier].spec.dependencies:
                if dependency not in self.stages:
                    raise ValueError(f"Unknown upstream stage: {dependency}")
                if dependency in selected:
                    visit(dependency)
            visiting.remove(identifier)
            done.add(identifier)
            result.append(identifier)

        for identifier in selected:
            visit(identifier)
        return tuple(result)

    def validate(self, configuration: PipelineConfiguration) -> None:
        if configuration.stages.keys() - self.stages.keys():
            raise ValueError("Configuration names an unregistered stage")
        self.order(tuple(self.stages))
        for identifier, stage in self.stages.items():
            options = configuration.stages.get(identifier, StageOptions())
            checks = {c.spec.id: c for c in stage.checks}
            if options.checks.keys() - checks.keys():
                raise ValueError(f"Unknown check configuration for {identifier}")
            done: set[str] = set()
            for check in stage.checks:
                check.validate_configuration(options.checks.get(check.spec.id, CheckOptions()))
                if not set(check.spec.dependencies) <= done:
                    raise ValueError(
                        f"Missing, unordered or cyclic check dependency: {check.spec.id}"
                    )
                done.add(check.spec.id)
                enabled = options.checks.get(check.spec.id, CheckOptions())
                if enabled.enabled:
                    for dependency in check.spec.dependencies:
                        if dependency in options.checks and not options.checks[dependency].enabled:
                            raise ValueError("An enabled check requires a disabled dependency")

    def fingerprint(self, identifier: str, configuration: PipelineConfiguration) -> str:
        stage = self.stages[identifier]
        return fingerprint(
            stage=stage.spec.model_dump(),
            checks=[c.spec.model_dump() for c in stage.checks],
            configuration=configuration.stages.get(identifier, StageOptions()).model_dump(),
        )
