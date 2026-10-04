# Document Management System

A simple, open-source Document Management System built with FastAPI and React. This repository is intended as a beginner-friendly project for open-source contributions.

## Features

- **User Authentication:** Register and log in securely with JWT
- Upload and manage personal documents (up to 10MB)
- Documents and folders are completely private to the owner
- Download documents
- Delete documents
- Create and delete folders
- Search documents by filename

## Tech Stack

- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic
- **Frontend:** React, Vite, Tailwind CSS, Axios

## Requirements

- Python 3.11+
- Node.js (v18+ recommended)
- PostgreSQL

## Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/your-username/DocVault.git
cd DocVault
```

### 2. Backend Setup

Configure PostgreSQL:
- Ensure PostgreSQL is running.
- Create a database (e.g., `document_manager`).

Set up Python environment and run:
```bash
cd backend
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
# source venv/bin/activate

pip install -r requirements.txt

# Create .env file and set your DATABASE_URL
copy .env.example .env
# Edit .env with your PostgreSQL credentials

# Run database migrations to create tables
alembic upgrade head

# Start the application
uvicorn app.main:app --reload
```
The API will be available at `http://localhost:8000`.

### 3. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```
The React application will be available at `http://localhost:5173`.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to get started.

Check out our [GOOD_FIRST_ISSUES.md](GOOD_FIRST_ISSUES.md) for ideas on what to work on.
