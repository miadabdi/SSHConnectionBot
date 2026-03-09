import ipaddress


def is_valid_host(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False

    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return all(c.isalnum() or c in ".-_" for c in candidate)


def is_valid_port(value: str) -> bool:
    try:
        port = int(value.strip())
    except ValueError:
        return False
    return 1 <= port <= 65535


def is_valid_slug(value: str) -> bool:
    value = value.strip().lower()
    return bool(value) and all(c.isalnum() or c in "-_" for c in value)
