"""Small deployment and per-device port checks shared by the engine and UI."""
import os


def port_set(value: str) -> set[int]:
    ports = set()
    for item in value.split(","):
        if not item.strip():
            continue
        bounds = [int(part) for part in item.strip().split("-")]
        if len(bounds) > 2 or not 1 <= bounds[0] <= bounds[-1] <= 65535:
            raise ValueError("Invalid configured port range")
        ports.update(range(bounds[0], bounds[-1] + 1))
    return ports


def validate_ports(analyzer: dict, others: list[dict]) -> None:
    port = analyzer["listenPort"]
    if not 0 <= port <= 65535:
        raise ValueError("listenPort must be 0 (disabled) or between 1 and 65535")
    if not 1 <= analyzer["resultDestinationPort"] <= 65535:
        raise ValueError("resultDestinationPort must be between 1 and 65535")
    reserved = port_set(os.environ.get("SIMULATOR_RESERVED_PORTS", "8090"))
    if port and port in reserved:
        raise ValueError(f"Listener port {port} is reserved")
    allowed = port_set(os.environ.get("SIMULATOR_ALLOWED_LISTEN_PORTS", ""))
    if port and allowed and port not in allowed:
        raise ValueError(f"Listener port {port} is not in the published/allowed pool")
    if port and any(other["id"] != analyzer["id"] and other.get("listenPort") == port for other in others):
        raise ValueError(f"Listener port {port} is already assigned to another analyzer")

