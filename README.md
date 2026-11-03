# VenueHub — Render + Neon PostgreSQL Deployment

VenueHub is a Flask-based community hall booking and management application. It uses Neon PostgreSQL for persistent cloud storage and is configured to run on Render with Gunicorn.

## Features
- Public hall listing and booking requests (customers do not need accounts).
- Admin-only login and dashboard.
- Booking approval, rejection and cancellation.
- Hall management, maintenance scheduling and reports.
- Admin Settings: change admin name/email/password and edit venue contact details.
- PostgreSQL database hosted by Neon.

## Deploy on Neon + Render

### 1. Create a Neon database
1. Create a project in Neon.
2. Open the project dashboard and copy the **pooled connection string** (recommended for serverless/web hosting).
3. Ensure the connection string includes SSL. It usually contains `sslmode=require`.
4. Keep the connection string private. Do not paste it into source code or commit it to GitHub.

### 2. Push this project to GitHub
1. Extract the ZIP.
2. Create a new GitHub repository.
3. Upload the contents of the `VenueHub` folder (the `app.py` file should be at the repository root).
4. Commit and push the files.

### 3. Create the Render web service
1. Sign in to Render and choose **New → Blueprint** (if deploying with `render.yaml`) or **New → Web Service**.
2. Connect your GitHub repository.
3. For manual Web Service configuration use:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
4. Add these environment variables in Render → Environment:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `SECRET_KEY` | A long, random secret value (Render can generate one) |
| `ADMIN_EMAIL` | Your chosen admin login email |
| `ADMIN_PASSWORD` | A strong initial password of at least 12 characters |
| `ADMIN_NAME` | Your preferred admin display name |

`ADMIN_PASSWORD` is used only when the first administrator is created in an empty database. After that, change credentials from the VenueHub **Settings** page. Changing the Render variable later does not reset an existing admin password.

### 4. Deploy
1. Save the environment variables.
2. Deploy the service.
3. Render will install the packages and start the Flask app with Gunicorn.
4. Open the Render service URL and sign in at `/login`.

### 5. First login
Use the `ADMIN_EMAIL` and `ADMIN_PASSWORD` values you set in Render. Then open **Settings** to update the administrator profile and venue details.

## Important notes
- The database schema is created automatically when the app starts.
- PostgreSQL is the only database used by this deployment build; it does not create a local SQLite database.
- Do not commit `.env`, database credentials, or secret keys.
- Use a Neon pooled connection string for the deployed web service.
- The initial admin is created only when the database has no admin account. If you lose the admin password, use a secure database-admin recovery process rather than expecting `ADMIN_PASSWORD` to overwrite the existing account.
- Free hosting plans may sleep or have usage limits depending on provider policies. Check current Render and Neon plan limits before production use.

## Run locally
1. Install Python 3.10 or newer.
2. Install PostgreSQL and create a database, or use a Neon database.
3. Set environment variables (`DATABASE_URL`, `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`).
4. Install dependencies: `pip install -r requirements.txt`
5. Start: `python app.py`

On Windows PowerShell, set variables for the current terminal session with:
```powershell
$env:DATABASE_URL="your-neon-connection-string"
$env:SECRET_KEY="a-long-random-secret"
$env:ADMIN_EMAIL="you@example.com"
$env:ADMIN_PASSWORD="a-strong-password-of-at-least-12-characters"
$env:ADMIN_NAME="VenueHub Administrator"
python app.py
```
