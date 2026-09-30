import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import install_basic_auth
from app.api.errors import install_error_handlers
from app.api.openapi import install_openapi
from app.api.routes import router
from app.config.rubric import load_rubric
from app.pipeline.runner import Runner
from app.settings import PIPELINE_VERSION, Settings, get_settings
from app.storage.db import Store
from app.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        store = Store(settings.database_url)
        store.init(auto_migrate=settings.auto_run_migrations)
        if swept := store.sweep_interrupted():
            log.warning("marked %d unfinished analyses as interrupted_by_restart", swept)

        rubric = load_rubric()
        worker = Worker(Runner(settings, store, rubric).run)
        if settings.run_worker:
            worker.start()

        app.state.settings = settings
        app.state.store = store
        app.state.rubric = rubric
        app.state.worker = worker
        yield
        worker.stop()
        store.close()

    app = FastAPI(title="Clip Scoring Service", version=PIPELINE_VERSION, lifespan=lifespan)
    install_error_handlers(app)
    install_basic_auth(app, settings)
    app.include_router(router)
    install_openapi(app)
    return app


app = create_app()
