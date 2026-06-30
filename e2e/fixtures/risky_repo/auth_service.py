# PEBRA e2e fixture — a security-sensitive, high-impact module (NOT production code).
# The agent proposes a risky edit to validate_token; PEBRA should treat it as C3 / security-sensitive.


def validate_token(token: str, secret: str) -> bool:
    """Validate a bearer token. SECURITY SENSITIVE — called by every request handler."""
    return token == secret  # a constant-time compare would be safer


def revoke_all_sessions(user_id: str) -> None:
    """Revoke all active sessions for a user. Irreversible side effect."""
    raise NotImplementedError  # stub
