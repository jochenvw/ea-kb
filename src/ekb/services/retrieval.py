import math
from collections.abc import Mapping


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embedding dimensions must agree and be nonempty")
    if not all(math.isfinite(v) for v in (*left, *right)):
        raise ValueError("Embedding vectors must be finite")
    left_scale = max(abs(value) for value in left)
    right_scale = max(abs(value) for value in right)
    if not left_scale or not right_scale:
        raise ValueError("Embedding vectors must be nonzero")
    left_normalized = tuple(value / left_scale for value in left)
    right_normalized = tuple(value / right_scale for value in right)
    denominator = math.sqrt(
        sum(value * value for value in left_normalized)
        * sum(value * value for value in right_normalized)
    )
    return sum(x * y for x, y in zip(left_normalized, right_normalized, strict=True)) / denominator


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
