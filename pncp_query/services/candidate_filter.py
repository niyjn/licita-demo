from dataclasses import dataclass, field

from pncp_query.services.common import somente_digitos


@dataclass(frozen=True)
class CandidateDecision:
    accepted: bool
    cnpj: str
    reason: str = ""
    details: dict = field(default_factory=dict)


class CandidateFilter:
    def evaluate(self, cnpj, buyer_org_cnpj=None, source_org_cnpj=None):
        normalized = somente_digitos(cnpj)
        if not normalized or len(normalized) != 14:
            return CandidateDecision(False, normalized, "empty_or_malformed", {"raw_cnpj": str(cnpj or "")})
        if not cnpj_valido(normalized):
            return CandidateDecision(False, normalized, "invalid_cnpj", {"raw_cnpj": str(cnpj or "")})

        buyer = somente_digitos(buyer_org_cnpj)
        if buyer and normalized == buyer:
            return CandidateDecision(False, normalized, "buyer_org_cnpj", {"buyer_org_cnpj": buyer})

        source = somente_digitos(source_org_cnpj)
        if source and normalized == source:
            return CandidateDecision(False, normalized, "source_org_cnpj", {"source_org_cnpj": source})

        return CandidateDecision(True, normalized)


def cnpj_valido(cnpj):
    digits = somente_digitos(cnpj)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False

    def check_digit(base):
        weights = list(range(len(base) - 7, 1, -1)) + list(range(9, 1, -1))
        total = sum(int(digit) * weight for digit, weight in zip(base, weights, strict=False))
        remainder = total % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(digits[:12])
    second = check_digit(digits[:12] + first)
    return digits[-2:] == first + second
