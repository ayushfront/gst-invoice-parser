from fastapi.responses import JSONResponse


def error_response(code: str, message: str, detail: str = "", status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "detail": detail,
            },
        },
    )
