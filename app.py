from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, join_room
import psycopg2
import psycopg2.extras
from psycopg.rows import dict_row
import random
import string
import os
from config import Config


# ---------------- APP INIT ----------------
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# IMPORTANT for production (no eventlet)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


def get_db_connection():
    return psycopg2.connect(app.config["DATABASE_URL"])



def create_tables():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS polls (
                    id VARCHAR(10) PRIMARY KEY,
                    question TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS options (
                    id SERIAL PRIMARY KEY,
                    poll_id VARCHAR(10) REFERENCES polls(id) ON DELETE CASCADE,
                    option_text TEXT NOT NULL,
                    vote_count INTEGER DEFAULT 0
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS votes (
                    id SERIAL PRIMARY KEY,
                    poll_id VARCHAR(10),
                    ip_address VARCHAR(50),
                    UNIQUE (poll_id, ip_address)
                );
            """)


# ---------------- GENERATE POLL ID ----------------
def generate_poll_id(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


# ---------------- HOME PAGE ----------------
@app.route("/")
def home():
    return render_template("create_poll.html")


# ---------------- CREATE POLL ----------------
@app.route("/create", methods=["POST"])
def create_poll():
    question = request.form.get("question")

    options = [
        request.form.get("option1"),
        request.form.get("option2"),
        request.form.get("option3"),
        request.form.get("option4"),
    ]

    options = [opt for opt in options if opt and opt.strip() != ""]

    if not question or len(options) < 2:
        return "Poll must have a question and at least 2 options."

    poll_id = generate_poll_id()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:

                cursor.execute(
                    "INSERT INTO polls (id, question) VALUES (%s, %s)",
                    (poll_id, question)
                )

                for opt in options:
                    cursor.execute(
                        "INSERT INTO options (poll_id, option_text) VALUES (%s, %s)",
                        (poll_id, opt)
                    )

    except Exception:
        return "Error creating poll."

    share_url = url_for("view_poll", poll_id=poll_id, _external=True)
    return render_template("poll_created.html", share_url=share_url)


# ---------------- VIEW POLL ----------------
@app.route("/poll/<poll_id>")
def view_poll(poll_id):

    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:

            cursor.execute("SELECT * FROM polls WHERE id = %s", (poll_id,))
            poll = cursor.fetchone()

            if not poll:
                return "Poll not found."

            cursor.execute("SELECT * FROM options WHERE poll_id = %s", (poll_id,))
            options = cursor.fetchall()

    return render_template("poll_room.html", poll=poll, options=options)


# ---------------- VOTE ----------------
@app.route("/vote/<poll_id>", methods=["POST"])
def vote(poll_id):

    data = request.get_json()
    option_id = data.get("option_id")

    if not option_id:
        return jsonify({"success": False, "message": "Invalid vote."})

    # -------- Session Restriction --------
    if "voted_polls" not in session:
        session["voted_polls"] = []

    if poll_id in session["voted_polls"]:
        return jsonify({"success": False, "message": "You already voted (session)."})

    ip_address = request.remote_addr

    try:
        with get_db_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:

                # IP restriction
                cursor.execute(
                    "SELECT * FROM votes WHERE poll_id = %s AND ip_address = %s",
                    (poll_id, ip_address)
                )
                existing_vote = cursor.fetchone()

                if existing_vote:
                    return jsonify({"success": False, "message": "You already voted (IP restriction)."})

                # Insert vote record
                cursor.execute(
                    "INSERT INTO votes (poll_id, ip_address) VALUES (%s, %s)",
                    (poll_id, ip_address)
                )

                # Increment vote count
                cursor.execute(
                    "UPDATE options SET vote_count = vote_count + 1 WHERE id = %s",
                    (option_id,)
                )

                # Fetch updated results
                cursor.execute(
                    "SELECT id, vote_count FROM options WHERE poll_id = %s",
                    (poll_id,)
                )
                updated_results = cursor.fetchall()

        # Update session
        session["voted_polls"].append(poll_id)
        session.modified = True

        # Emit real-time update
        socketio.emit("update_results", updated_results, room=poll_id)

        return jsonify({"success": True})

    except Exception:
        return jsonify({"success": False, "message": "Vote failed."})


# ---------------- SOCKET JOIN ----------------
@socketio.on("join_poll")
def handle_join(data):
    poll_id = data["poll_id"]
    join_room(poll_id)


# ---------------- START APP ----------------
if __name__ == "__main__":
    if os.environ.get("RENDER") is None:
        # Only run table creation locally
        create_tables()
        socketio.run(app, debug=True)
