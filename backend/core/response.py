"""Consistent JSON response helpers and error handling."""
from typing import Any, Optional
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def ok(data: Any = None, message: str = "", pagination: Optional[dict] = None,
       status_code: int = 200):
    payload = {"success": True, "data": data, "message": message}
    if pagination is not None:
        payload["pagination"] = pagination
    return JSONResponse(status_code=status_code, content=payload)


def created(data: Any = None, message: str = "Created"):
    return ok(data=data, message=message, status_code=201)


def error(message: str, code: str = "ERROR", status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": message, "code": code,
                 "statusCode": status_code},
    )


class APIError(HTTPException):
    def __init__(self, message: str, code: str = "ERROR", status_code: int = 400):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


async def api_error_handler(request: Request, exc: APIError):
    return error(exc.detail, exc.code, exc.status_code)


async def http_error_handler(request: Request, exc: HTTPException):
    return error(str(exc.detail), "HTTP_ERROR", exc.status_code)


async def generic_error_handler(request: Request, exc: Exception):
    return error(str(exc) or "Internal Server Error", "INTERNAL_ERROR", 500)


def paginate(items: list, total: int, page: int, limit: int) -> dict:
    total_pages = (total + limit - 1) // limit if limit else 1
    return {"total": total, "page": page, "limit": limit, "totalPages": total_pages}
