from dataclasses import dataclass
from fractions import Fraction


def _polynomial(values) -> tuple[Fraction, ...]:
    coefficients = []
    for value in values:
        if not isinstance(value, Fraction) and type(value) not in (int, str):
            raise ValueError('polynomial coefficients must be exact numbers')
        coefficients.append(Fraction(value))
    while len(coefficients) > 1 and not coefficients[-1]:
        coefficients.pop()
    return tuple(coefficients) or (Fraction(0),)


@dataclass(frozen=True, eq=False, slots=True)
class RationalTimeLaw:
    _operator: str
    _mass: Fraction
    _operands: tuple['RationalTimeLaw', ...] = ()
    _numerator: tuple[Fraction, ...] = ()
    _denominator: tuple[Fraction, ...] = ()

    @classmethod
    def from_polynomials(cls, numerator, denominator=(1,)) -> 'RationalTimeLaw':
        """Build an elementary nonnegative series P/(1-Q), with Q(0)=0 and Q(1)<1."""
        top, bottom = _polynomial(numerator), _polynomial(denominator)
        scale = bottom[0]
        if not scale:
            raise ValueError('denominator must have a nonzero constant')
        top = tuple(value / scale for value in top)
        bottom = tuple(value / scale for value in bottom)
        if any(value < 0 for value in top) or any(value > 0 for value in bottom[1:]) or sum(bottom) <= 0:
            raise ValueError('polynomials do not define an elementary nonnegative convergent series')
        return cls('poly', sum(top) / sum(bottom), (), top, bottom)

    def total_mass(self) -> Fraction:
        return self._mass

    def add(self, other: 'RationalTimeLaw') -> 'RationalTimeLaw':
        if not self._mass:
            return other
        if not other._mass:
            return self
        return RationalTimeLaw('sum', self._mass + other._mass, (self, other))

    def multiply(self, other: 'RationalTimeLaw') -> 'RationalTimeLaw':
        if not self._mass or not other._mass:
            return ZERO
        if self is ONE:
            return other
        if other is ONE:
            return self
        return RationalTimeLaw('product', self._mass * other._mass, (self, other))

    def repeat(self) -> 'RationalTimeLaw':
        if self._mass >= 1:
            raise ValueError('repeated series must have total mass less than one')
        if not self._mass:
            return ONE
        return RationalTimeLaw('repeat', 1 / (1 - self._mass), (self,))

    def _evaluation_order(self) -> list['RationalTimeLaw']:
        ordered = []
        finished = set()
        pending = [(self, False)]
        while pending:
            node, expanded = pending.pop()
            if node in finished:
                continue
            if expanded:
                finished.add(node)
                ordered.append(node)
            else:
                pending.append((node, True))
                pending.extend((child, False) for child in reversed(node._operands))
        return ordered

    def coefficient_prefix(self, last_minute: int) -> tuple[Fraction, ...]:
        if type(last_minute) is not int or last_minute < 0:
            raise ValueError('last minute must be a nonnegative integer')
        coefficients = {}
        size = last_minute + 1
        for node in self._evaluation_order():
            inputs = tuple(coefficients[child] for child in node._operands)
            if node._operator == 'poly':
                values = node._elementary_prefix(size)
            elif node._operator == 'sum':
                left, right = inputs
                values = [a + b for a, b in zip(left, right)]
            elif node._operator == 'product':
                left, right = inputs
                values = [sum((left[i] * right[n - i] for i in range(n + 1)), Fraction(0)) for n in range(size)]
            else:
                (loop,) = inputs
                values = [1 / (1 - loop[0])]
                for n in range(1, size):
                    values.append(sum(loop[i] * values[n - i] for i in range(1, n + 1)) / (1 - loop[0]))
            coefficients[node] = values
        return tuple(coefficients[self])

    def _elementary_prefix(self, size: int) -> list[Fraction]:
        values = []
        for n in range(size):
            direct = self._numerator[n] if n < len(self._numerator) else Fraction(0)
            feedback = sum(self._denominator[i] * values[n - i] for i in range(1, min(n + 1, len(self._denominator))))
            values.append(direct - feedback)
        return values

    def to_wire(self) -> list:
        program = []
        indices = {}
        for node in self._evaluation_order():
            indices[node] = len(program)
            if node._operator == 'poly':
                program.append(['poly', list(map(str, node._numerator)), list(map(str, node._denominator))])
            else:
                program.append([node._operator, *(indices[child] for child in node._operands)])
        return program

    @classmethod
    def from_wire(cls, program: list) -> 'RationalTimeLaw':
        if not isinstance(program, list) or not program:
            raise ValueError('missing time-law program')
        nodes = []
        for instruction in program:
            if not isinstance(instruction, list) or not instruction:
                raise ValueError('invalid time-law instruction')
            op, *args = instruction
            if op == 'poly' and len(args) == 2:
                if not all(isinstance(poly, list) and poly and all(type(value) is str for value in poly) for poly in args):
                    raise ValueError('invalid encoded polynomial')
                node = cls.from_polynomials(*args)
            elif op in ('sum', 'product', 'repeat'):
                arity = 1 if op == 'repeat' else 2
                if len(args) != arity or any(type(index) is not int or not 0 <= index < len(nodes) for index in args):
                    raise ValueError('invalid time-law reference')
                inputs = [nodes[index] for index in args]
                if op == 'sum':
                    node = inputs[0].add(inputs[1])
                elif op == 'product':
                    node = inputs[0].multiply(inputs[1])
                else:
                    node = inputs[0].repeat()
            else:
                raise ValueError('unknown time-law instruction')
            nodes.append(node)
        return nodes[-1]


ZERO = RationalTimeLaw.from_polynomials((0,))
ONE = RationalTimeLaw.from_polynomials((1,))
