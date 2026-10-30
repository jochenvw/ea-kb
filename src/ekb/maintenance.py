from ekb.adapters.persistence.postgres import Postgres


async def reset_workspace(database: Postgres, *, recreate: bool = False) -> None:
    """Reset EKB-owned database state without deleting source originals or inputs."""
    async with database.reset(recreate=recreate):
        pass
