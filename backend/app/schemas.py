from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

# User Schemas
class UserBase(BaseModel):
    username: str
    email: str

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Auth Schemas
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# Document Schemas
class DocumentBase(BaseModel):
    filename: str
    original_filename: str
    file_size: int
    content_type: str
    folder_id: Optional[int] = None

class DocumentCreate(DocumentBase):
    file_path: str

class DocumentResponse(DocumentBase):
    id: int
    created_at: datetime
    file_path: str
    owner_id: int

    model_config = ConfigDict(from_attributes=True)

# Folder Schemas
class FolderBase(BaseModel):
    name: str

class FolderCreate(FolderBase):
    pass

class FolderResponse(FolderBase):
    id: int
    owner_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
