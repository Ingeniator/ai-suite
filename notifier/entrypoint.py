import uvicorn

from notifier.main import app  # noqa: F401

if __name__ == "__main__":
    uvicorn.run(
        "entrypoint:app",
        host="0.0.0.0",
        port=5030,
        log_level="info",
    )
