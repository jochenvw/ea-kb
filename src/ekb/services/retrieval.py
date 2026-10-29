import math
from collections.abc import Mapping


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embedding dimensions must agree and be nonempty")
    if not all(math.isfinite(v) for v in (*left, *right)):
        raise ValueError("Embedding vectors must be finite")
    denominator = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
    if not denominator:
        raise ValueError("Embedding vectors must be nonzero")
    return sum(x * y for x, y in zip(left, right, strict=True)) / denominator


def nearest[T](
    query: tuple[float, ...],
    vectors: Mapping[T, tuple[float, ...]],
    limit: int,
) -> list[tuple[T, float]]:
    return sorted(
        ((key, cosine(query, vector)) for key, vector in vectors.items()),
        key=lambda item: (-item[1], str(item[0])),
    )[: max(0, limit)]


def vector_pairs[T](
    vectors: Mapping[T, tuple[float, ...]],
    neighbors: int,
    limit: int,
) -> list[tuple[T, T, float]]:
    pairs = {}
    for key, vector in vectors.items():
        neighbors_found = [
            pair for pair in nearest(vector, vectors, neighbors + 1) if pair[0] != key
        ][: max(0, neighbors)]
        for other, score in neighbors_found:
            left, right = sorted((key, other), key=str)
            pairs[(left, right)] = score
    return [
        (left, right, score)
        for (left, right), score in sorted(
            pairs.items(), key=lambda item: (-item[1], str(item[0]))
        )[:limit]
    ]
