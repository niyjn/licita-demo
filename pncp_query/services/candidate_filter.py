from pncp_query.services.common import somente_digitos


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
