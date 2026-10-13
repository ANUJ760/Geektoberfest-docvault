from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base
from .routes import documents, folders, auth_routes, audit
import os

# Tables will be created using Alembic migrations


app = FastAPI(title="Document Management System API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://doc-vault-lac.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload directory exists
os.makedirs("uploads", exist_ok=True)

app.include_router(auth_routes.router, prefix="/auth", tags=["auth"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(folders.router, prefix="/folders", tags=["folders"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])

@app.get("/")
def read_root():
    return {"message": "Welcome to Document Management System API"}
