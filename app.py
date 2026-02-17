from flask import Flask, render_template, request, url_for, jsonify, session
from flask_socketio import SocketIO, join_room
import psycopg2
import psycopg2.extras
import random
import string
import os
from config import Config

# ---------------- APP INIT ----------------
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# DO NOT set async_mode here (eventlet will handle it)
socketio = SocketIO(app, cors_allowed_origins="*")


# ---------------- DATABASE CONNECTION ----------------
def get_db_connection():
    return psycopg2.connect(app.config["DATABASE_URL"])


# ---------------- CREATE TABLES ----------------
def create_tables():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

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
                ip_address VARCHAR(100),
                UNIQUE (poll_id, ip_address)
            );
        """)

        conn.commit()
        cursor.close()
        conn.close()

        print("Tables created successfully")

    except Exception as e:
        print("Table creation error:", e)


# ---------------- AUTO CREATE TABLES ----------------
create_tables()


# ---------------- GENERATE POLL ID ----------------
def generate_poll_id(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


# ---------------- HOME ----------------
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
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO polls (id, question) VALUES (%s, %s)",
            (poll_id, question)
        )

        for opt in options:
            cursor.execute(
                "INSERT INTO options (poll_id, option_text) VALUES (%s, %s)",
                (poll_id, opt)
            )

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        print("Create poll error:", e)
        return "Error creating poll."

    share_url = url_for("view_poll", poll_id=poll_id, _external=True)
    return render_template("poll_created.html", share_url=share_url)


# ---------------- VIEW POLL ----------------
@app.route("/poll/<poll_id>")
def view_poll(poll_id):

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM polls WHERE id = %s", (poll_id,))
        poll = cursor.fetchone()

        if not poll:
            return "Poll not found."

        cursor.execute("SELECT * FROM options WHERE poll_id = %s", (poll_id,))
        options = cursor.fetchall()

        cursor.close()
        conn.close()

        return render_template("poll_room.html", poll=poll, options=options)

    except Exception as e:
        print("View poll error:", e)
        return "Error loading poll."


# ---------------- VOTE ----------------
@app.route("/vote/<poll_id>", methods=["POST"])
def vote(poll_id):

    data = request.get_json()
    option_id = data.get("option_id")

    if not option_id:
        return jsonify({"success": False, "message": "Invalid vote."})

    # Session restriction
    if "voted_polls" not in session:
        session["voted_polls"] = []

    if poll_id in session["voted_polls"]:
        return jsonify({"success": False, "message": "You already voted."})

    # 🔥 FIXED IP DETECTION FOR RENDER
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # IP restriction
        cursor.execute(
            "SELECT * FROM votes WHERE poll_id = %s AND ip_address = %s",
            (poll_id, ip_address)
        )

        if cursor.fetchone():
            return jsonify({"success": False, "message": "You already voted (IP)."})

        cursor.execute(
            "INSERT INTO votes (poll_id, ip_address) VALUES (%s, %s)",
            (poll_id, ip_address)
        )

        cursor.execute(
            "UPDATE options SET vote_count = vote_count + 1 WHERE id = %s",
            (option_id,)
        )

        conn.commit()

        cursor.execute(
            "SELECT id, vote_count FROM options WHERE poll_id = %s",
            (poll_id,)
        )
        updated_results = cursor.fetchall()

        cursor.close()
        conn.close()

        session["voted_polls"].append(poll_id)
        session.modified = True

        socketio.emit("update_results", updated_results, room=poll_id)

        return jsonify({"success": True})

    except Exception as e:
        print("Vote error:", e)
        return jsonify({"success": False, "message": "Vote failed."})


# ---------------- SOCKET JOIN ----------------
@socketio.on("join_poll")
def handle_join(data):
    join_room(data["poll_id"])


# ---------------- RUN LOCALLY ----------------
if __name__ == "__main__":
    socketio.run(app, debug=True)
