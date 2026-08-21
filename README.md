# Inventory Management System

An automated, AI-driven inventory management system designed to transition from manual Excel tracking to a smart, predictive web application.

## 🚀 Features

*   **Inventory Dashboard**: Real-time tracking of parts and machine stock items.
*   **Parts Management**: Add, edit, delete, outgoing/returning flows, serial/items tracking, and faulty returns reporting.
*   **Machine Stock (Machine Registry)**: Track machine stock items with serial management.
*   **Vendor Search (Parts + Machines)**:
    *   Best-effort price extraction (USD/MYR) with auto currency conversion when only one currency is available.
    *   Filters results to the **last 6 months**.
    *   Hides results where a price cannot be extracted.
    *   Sorting by Relevance / USD / MYR.
*   **Machine Image Upload**: Upload and view a machine stock image.
*   **User Authentication & Roles**:
    *   First-time registration creates the first user as **Admin**.
    *   Registration link is only visible when there are **no users** yet.
    *   Roles: **Admin** and **User** (Admin can manage users).
*   **Admin User Management**: Create users, edit users, disable users, and reset passwords.
*   **Reports & Logs**: Activity log, movements, rack inventory report, and client faulty log.

## 🛠️ Tech Stack

*   **Backend**: Python 3.10+, FastAPI
*   **Database**: SQLite (SQLAlchemy ORM)
*   **Frontend**: Bootstrap 5, Jinja2 Templates
*   **Authentication**: JWT (JSON Web Tokens)

## 📋 Prerequisites

*   Python 3.10 or higher
*   pip (Python package manager)

## ⚙️ Installation & Setup

1.  **Clone the repository** (if applicable) or navigate to the project directory:
    ```bash
    cd "Inventory Management System"
    ```

2.  **Create a Virtual Environment** (Recommended):
    ```bash
    python -m venv venv
    
    # Windows
    venv\Scripts\activate
    
    # macOS/Linux
    source venv/bin/activate
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Initialize the Database**:
    Run the seed script to create the database tables and populate them with sample data (33 SKUs):
    ```bash
    python seed.py
    ```
    *This will create an `inventory.db` file in your project root.*

## 🚀 Running the Application

1.  **Start the Server**:
    ```bash
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    ```

2.  **Access the App**:
    Open your web browser and go to:
    **[http://localhost:8000](http://localhost:8000)** or use your machine's IP address.

## 👤 Usage Guide

1.  **Initial Setup (First Run)**:
    *   Open **/login** and use **Sign up** to create the first account.
    *   The first registered user is created as **Admin** automatically.
2.  **Login**: Use your email and password to access the dashboard.
3.  **Settings**:
    *   **Email Setting**: Configure SMTP and test email.
    *   **User Management** (Admin): Create/edit/disable users and reset user passwords.
4.  **Vendor Sources**:
    *   Use Vendor Sources on Parts and Machines to find recent (last 6 months) vendor listings and prices.

## 🧪 Running Tests (Optional)

To run the test suite (if configured):
```bash
pytest
```

## 📂 Project Structure

```
Inventory Management System/
├── app/
│   ├── main.py                 # Application entry point (routes + UI pages)
│   ├── models.py               # Database models
│   ├── schemas.py              # Pydantic schemas
│   ├── crud.py                 # Database operations
│   ├── auth.py                 # Authentication logic (JWT, password hashing)
│   ├── database.py             # Database connection/session
│   ├── templates/              # Jinja2 templates
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── settings.html
│   │   ├── reset_password.html
│   │   ├── dashboard.html
│   │   ├── vendor_sources.html
│   │   ├── machine_vendor_sources.html
│   │   ├── activity_log.html
│   │   ├── movements.html
│   │   ├── rack_inventory.html
│   │   ├── faulty_parts.html
│   │   └── client_faulty_log.html
│   └── static/
│       └── machine_registry/   # Uploaded machine images
├── inventory.db         # SQLite database (generated)
├── seed.py              # Script to populate sample data
├── requirements.txt     # Python dependencies
└── README.md            # Project documentation
```
