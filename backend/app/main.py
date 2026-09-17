from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
import os

from .db import init_db
from .auth import get_session_secret
from .api.routes import router
from .api.auth_routes import router as auth_router

app = FastAPI(title="Codebase Navigator", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
# Session cookie must come after CORS in the middleware stack (Starlette
                                                                    
                                                                            
                                                                  
app.add_middleware(SessionMiddleware, secret_key=get_session_secret(), same_site="lax")

init_db()
app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}


                                                                         
                                                       
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
