import uvicorn

from .settings import WEB_HOST, WEB_PORT


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=WEB_HOST, port=WEB_PORT, log_level="info")
