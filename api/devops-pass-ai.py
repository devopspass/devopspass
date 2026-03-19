import os

import uvicorn


if __name__ == "__main__":
    fastapi_reload = os.environ.get("FASTAPI_RELOAD", "true").lower() == "true"

    uvicorn.run(
        "main:app",
        host=os.environ.get("FASTAPI_HOST", "0.0.0.0"),
        port=int(os.environ.get("FASTAPI_PORT", "10818")),
        reload=fastapi_reload,
        reload_dirs=[os.path.dirname(__file__), os.path.join(os.path.dirname(__file__), "..", "plugins")],
    )
