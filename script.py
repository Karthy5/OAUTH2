from flask import Flask, redirect, url_for, session, request, render_template_string
from authlib.integrations.flask_client import OAuth
import os
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from datetime import datetime, timezone
import logging
import pytz
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.DEBUG)

# Create Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Configure OAuth
oauth = OAuth(app)

# Register Google OAuth
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile https://www.googleapis.com/auth/calendar',
    }
)

def build_calendar_service():
    """Build the Google Calendar API service."""
    if 'user_token' not in session:
        return None

    token = session['user_token']
    credentials = Credentials(
        token=token['access_token'],
        refresh_token=token.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    )

    # Refresh the token if needed
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    return build('calendar', 'v3', credentials=credentials)

@app.route('/')
def home():
    user = session.get('user')
    if user:
        return f"""
        <h1>Hello, {user['name']}!</h1>
        <a href='/logout'>Logout</a> |
        <a href='/tasks'>View and Add Tasks</a>
        """
    return '<a href="/login">Login with Google</a>'

@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/callback')
def authorize():
    token = google.authorize_access_token()  # Exchange code for token
    session['user_token'] = token  # Save token in session
    userinfo_endpoint = google.server_metadata.get('userinfo_endpoint')  # Dynamically fetch userinfo endpoint
    user_info = google.get(userinfo_endpoint).json()  # Fetch user info
    session['user'] = user_info  # Save user info in session
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('user_token', None)
    return redirect('/')

@app.route('/tasks', methods=['GET', 'POST'])
def tasks():
    service = build_calendar_service()
    if not service:
        return redirect('/login')

    # HTML Template for displaying tasks and a form for adding new tasks
    html_template = """
    <h1>Task Manager</h1>
    <form method="post">
        <label for="title">Task Title:</label>
        <input type="text" name="title" id="title" required><br><br>
        
        <label for="description">Description:</label>
        <input type="text" name="description" id="description"><br><br>
        
        <label for="due_date">Due Date and Time:</label>
        <input type="datetime-local" name="due_date" id="due_date" required><br><br>
        
        <button type="submit">Add Task</button>
    </form>
    <hr>
    <h2>Upcoming Tasks</h2>
    <ul>
        {% for task in tasks %}
            <li>
                <strong>{{ task['summary'] }}</strong> ({{ task['start'] }}) - {{ task['description'] }}
                <a href="/delete/{{ task['id'] }}">Delete</a>
            </li>
        {% endfor %}
    </ul>
    {% if message %}
        <p>{{ message }}</p>
    {% endif %}
    <a href='/'>Back to Home</a>
    """

    if request.method == 'POST':
        task_title = request.form.get('title')
        task_description = request.form.get('description')
        task_due_date = request.form.get('due_date')  # Format: YYYY-MM-DDTHH:MM

        # Ensure seconds are included in the time if missing
        if len(task_due_date) == 16:  # If no seconds part is provided, append ':00'
            task_due_date += ':00'

        logging.debug(f"Received task due date: {task_due_date}")

        # Convert local time to UTC
        try:
            local_tz = pytz.timezone('Asia/Kolkata')  # Adjust the timezone if necessary
            local_time = datetime.strptime(task_due_date, "%Y-%m-%dT%H:%M:%S")
            local_time = local_tz.localize(local_time)  # Localize the time to your local timezone
            utc_time = local_time.astimezone(pytz.utc)  # Convert to UTC
            task_due_date_utc = utc_time.isoformat()

            logging.debug(f"Converted task due date to UTC: {task_due_date_utc}")
        except Exception as e:
            logging.error(f"Error converting local time to UTC: {str(e)}")
            return f"Error converting time: {str(e)}"

        # Create the event object
        try:
            event = {
                'summary': task_title,
                'description': task_description,
                'start': {
                    'dateTime': task_due_date_utc,
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': task_due_date_utc,
                    'timeZone': 'UTC',
                },
            }

            logging.debug(f"Event to add: {event}")

            event_result = service.events().insert(calendarId='primary', body=event).execute()

            logging.debug(f"Event added successfully: {event_result}")
            message = "Task added successfully!"
        except Exception as e:
            logging.error(f"Error adding task: {str(e)}")
            message = f"Error adding task: {str(e)}"

        return render_template_string(html_template, tasks=[], message=message)

    # For GET requests, list upcoming tasks
    try:
        now = datetime.now(timezone.utc).isoformat()  # Get current UTC time
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        tasks = []
        for event in events:
            tasks.append({
                "summary": event.get('summary', 'No Title'),
                "description": event.get('description', 'No Description'),
                "start": event.get('start', {}).get('dateTime', 'No Start Time'),
                "id": event['id'],  # Store the event ID for deletion
            })

        return render_template_string(html_template, tasks=tasks, message=None)
    except Exception as e:
        return f"Error fetching tasks: {str(e)}"

@app.route('/delete/<task_id>')
def delete_task(task_id):
    service = build_calendar_service()
    if not service:
        return redirect('/login')

    try:
        service.events().delete(calendarId='primary', eventId=task_id).execute()
        message = "Task deleted successfully!"
    except Exception as e:
        message = f"Error deleting task: {str(e)}"

    return redirect('/tasks?message=' + message)

if __name__ == '__main__':
    app.run(debug=True)
