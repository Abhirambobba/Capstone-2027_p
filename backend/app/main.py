import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from mangum import Mangum

from app.api.employees import router as employee_router
from app.api.integrations import router as integrations_router
from app.api.github_integrations import router as github_integrations_router
from app.api.me import router as me_router
from app.api.memberships import router as membership_router
from app.api.projects import router as project_router
from app.api.tasks import router as task_router

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ASTRA API", version="1.0.0")

app.include_router(employee_router)
app.include_router(project_router)
app.include_router(membership_router)
app.include_router(task_router)
app.include_router(me_router)
app.include_router(integrations_router)
app.include_router(github_integrations_router)

handler = Mangum(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8003")),
        reload=True,
    )