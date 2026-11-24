from fractions import Fraction

from review.network import ReleaseNetwork, solve_release_mass


def deadline_release(network: ReleaseNetwork, start: int, deadline: int) -> Fraction:
    if type(deadline) is not int or deadline < 0:
        raise ValueError('deadline must be a nonnegative integer')
    releases = [law.coefficient_prefix(deadline) for law in network.releases]
    handoffs = [(edge.source, edge.target, edge.law.coefficient_prefix(deadline)) for edge in network.handoffs]
    instant = [(source, target, weights[0]) for source, target, weights in handoffs if weights[0]]
    later = [(source, target, weights) for source, target, weights in handoffs if any(weights[1:])]
    cumulative_release = [Fraction(0) for _ in network.entries]
    history = []
    for budget in range(deadline + 1):
        for index, coefficients in enumerate(releases):
            cumulative_release[index] += coefficients[budget]
        direct = cumulative_release.copy()
        for source, target, weights in later:
            direct[source] += sum(
                weight * history[budget - elapsed][target]
                for elapsed, weight in enumerate(weights[1:budget + 1], 1)
                if weight
            )
        history.append(solve_release_mass(direct, instant))
    return history[-1][start]
