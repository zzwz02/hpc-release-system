"""Opt-in authentication for CI endpoints; never added to require_login()."""
from fastapi import Cookie, Depends, Header

from app.deps import get_db, require_login
from app.services import api_token_service


def owner_lookup_identity(authorization: str = Header(default=""), hpc_session: str | None = Cookie(default=None),
                          conn=Depends(get_db)):
    if authorization:
        return api_token_service.authenticate(conn, authorization, "owner_lookup")
    return require_login(hpc_session=hpc_session, conn=conn)
