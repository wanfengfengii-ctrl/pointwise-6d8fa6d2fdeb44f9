from fractions import Fraction

from review.network import ReleaseNetwork, solve_release_mass


def eventual_release(network: ReleaseNetwork, start: int) -> Fraction:
    direct = [law.total_mass() for law in network.releases]
    transfers = [(edge.source, edge.target, edge.law.total_mass()) for edge in network.handoffs]
    return solve_release_mass(direct, transfers)[start]
